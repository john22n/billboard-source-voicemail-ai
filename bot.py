import os
from typing import Any, cast

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.evals.transport import EvalTransportParams
from pipecat.flows import FlowManager
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
        )
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.workers.runner import WorkerRunner

from src.data_source.twilio import get_call_info
from src.flows.tools import submit_nutshell_lead, write_audit_event
from src.flows.voicemail import create_initial_node
from src.system_prompts.voicemail import initial_message

load_dotenv(override=True)

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
            settings = OpenAIRealtimeSTTService.Settings(
                model = "gpt-realtime-whisper",
                ),
            )

    # the brains of the operation recieves transcripts and generatees response text
    llm = OpenAILLMService(
            api_key = api_key,
            settings = OpenAILLMService.Settings(
                model = "gpt-4.1",
                system_instruction = initial_message,
                ),
            )

    # the mouth of the operation tts
    instructions = """Voice: Warm, upbeat, and reassuring, with a steady
    and confident cadence that keeps the conversation calm and productive.\n\n
    Tone: Positive and solution-oriented, always focusing on the next steps rather
    than dwelling on the problem.\n\nDialect: Neutral and professional,
    avoiding overly casual speech but maintaining a friendly and approachable style.
    \n\nPronunciation: Clear and precise, with a natural rhythm that emphasizes
    key words to instill confidence and keep the customer engaged.
    \n\nFeatures: Uses empathetic phrasing, gentle reassurance,
    and proactive language to shift the focus from frustration to resolution."""

    tts = OpenAITTSService(
            api_key = api_key,
            settings = OpenAITTSService.Settings(
                model = "tts-1-hd",
                voice = "alloy",
                instructions = instructions,
                speed = 1.10,
                ),
            )

    # store the conversation passed to the LLM
    context = LLMContext()

    # silero detects when the caller starts and stops speaking
    context_aggregator = LLMContextAggregatorPair(
            context,
            user_params = LLMUserAggregatorParams(
                vad_analyzer = SileroVADAnalyzer(),
                ),
            )

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
            idle_timeout_secs = runner_args.pipeline_idle_timeout_secs,
            )

    flow_manager = FlowManager(
            worker = worker,
            llm = cast(Any, llm),
            context_aggregator = context_aggregator,
            transport = transport,
            )
    flow_manager.state["llm_context"] = context

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client) -> None:
        logger.info("Twilio caller connected")
        write_audit_event("call_connected")

        call_sid = runner_args.call_data.call_id if runner_args.call_data else None
        call_info = await get_call_info(call_sid)
        if call_info and call_info.from_number:
            flow_manager.state["phone"] = call_info.from_number

        await flow_manager.initialize(create_initial_node())

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
