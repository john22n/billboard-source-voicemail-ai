import asyncio
import os
from typing import Any, cast

from dotenv import load_dotenv
from loguru import logger
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from pipecat.evals.transport import EvalTransportParams
from pipecat.flows import FlowManager
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.runner.types import (
    EvalRunnerArguments,
    RunnerArguments,
    SmallWebRTCRunnerArguments,
)
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.utils.tracing.setup import setup_tracing
from pipecat.workers.runner import WorkerRunner

from src.data_source.twilio import get_call_info
from src.flows.tools import submit_nutshell_lead, write_audit_event
from src.flows.voicemail import create_initial_node
from src.system_prompts.voicemail import initial_message

load_dotenv(override=True)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


IS_TRACING_ENABLED = _env_flag("ENABLE_TRACING")

if IS_TRACING_ENABLED:
    otlp_exporter = OTLPSpanExporter(
            endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
            insecure=True,
            )

    setup_tracing(
            service_name=os.getenv("OTEL_SERVICE_NAME", "voicemail-agent"),
            exporter=otlp_exporter,
            console_export=_env_flag("OTEL_CONSOLE_EXPORT"),
            )
    logger.info("OpenTelemetry tracing initialized")


transport_params = {
        "eval": lambda: EvalTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            ),
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            ),
        "twilio": lambda: FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            ),
        }


async def finalize_call(flow_manager: FlowManager) -> None:
    """Submit the lead after the call pipeline has finished."""
    write_audit_event("call_completed")
    await submit_nutshell_lead({}, flow_manager)


async def run_bot(
        transport: BaseTransport,
        runner_args: RunnerArguments,
        ) -> None:
    """ Create and run a voice agent for one phone call """

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    # the ears: stream the caller's audio to openai and emits text.
    stt = OpenAIRealtimeSTTService(
            api_key = api_key,
            turn_detection = {"type": "server_vad"},
            settings = OpenAIRealtimeSTTService.Settings(
                model = "gpt-4o-transcribe",
                ),
            )

    # the brains of the operation recieves transcripts and generatees response text
    llm = OpenAILLMService(
            api_key = api_key,
            settings = OpenAILLMService.Settings(
                model = "gpt-5.6-luna",
                system_instruction = initial_message,
                extra = {"reasoning_effort": "none"},
                ),
            )

    tts = OpenAITTSService(
            api_key = api_key,
            settings = OpenAITTSService.Settings(
                model = "gpt-4o-mini-tts",
                voice = "marin",
                speed = 1.15,
                ),
            )

    # store the conversation passed to the LLM
    context = LLMContext()

    # OpenAI STT detects when the caller starts and stops speaking.
    context_aggregator = LLMContextAggregatorPair(context)

    pipeline = Pipeline(
            [
                transport.input(),
                stt,
                context_aggregator.user(),
                llm,
                tts,
                transport.output(),
                context_aggregator.assistant(),
            ]
        )

    worker = PipelineWorker(
            pipeline,
            params = PipelineParams(
                enable_metrics = True,
                enable_usage_metrics = True,
                ),
            enable_tracing=IS_TRACING_ENABLED,
            enable_turn_tracking=IS_TRACING_ENABLED,
            idle_timeout_secs = runner_args.pipeline_idle_timeout_secs,
            )

    flow_manager = FlowManager(
            worker = worker,
            llm = cast(Any, llm),
            context_aggregator = context_aggregator,
            transport = transport,
            )
    flow_manager.state["llm_context"] = context
    flow_manager.state["nutshell_submission_enabled"] = not isinstance(
            runner_args,
            (EvalRunnerArguments, SmallWebRTCRunnerArguments),
            )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client) -> None:
        logger.info("Twilio caller connected")
        write_audit_event("call_connected")

        call_sid = runner_args.call_data.call_id if runner_args.call_data else None
        call_info_task = asyncio.create_task(get_call_info(call_sid))

        await flow_manager.initialize(create_initial_node())

        call_info = await call_info_task
        if call_info and call_info.from_number:
            flow_manager.state["calling_phone"] = call_info.from_number
        elif isinstance(runner_args, EvalRunnerArguments) and isinstance(
            runner_args.body, dict
        ):
            calling_phone = runner_args.body.get("calling_phone")
            if isinstance(calling_phone, str) and calling_phone.strip():
                flow_manager.state["calling_phone"] = calling_phone.strip()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client) -> None:
        logger.info("Twilio caller disconnected")
        write_audit_event("call_disconnected")
        await worker.cancel()

    worker_runner = WorkerRunner(
            handle_sigint = runner_args.handle_sigint,
            force_gc = True,
            )

    await worker_runner.add_workers(worker)
    try:
        await worker_runner.run()
    finally:
        await finalize_call(flow_manager)

async def bot(runner_args: RunnerArguments) -> None:
    """ entry point used by Pipecat's development runner"""
    try:
        logger.info("incoming twil connection")


        transport = await create_transport(
                runner_args,
                transport_params,
                )
        logger.info("transport create")
        await run_bot(transport, runner_args)
    except Exception:
        logger.exception("bot failed while starting the twilio call")
        raise
