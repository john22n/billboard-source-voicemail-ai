import os

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
        )
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.services.openai.responses.llm import OpenAIResponsesLLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)

transport_params = {
        "twilio": lambda: FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            ),
        }

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
    llm = OpenAIResponsesLLMService(
            api_key = api_key,
            settings = OpenAIResponsesLLMService.Settings(
                system_instruction = (
                    "You are a voicemail assistant for BillBoard Source billboard advertising company "
                    "speak naturally and briefly. ask one question at at time "
                    "Collect the callers name company phone number email "
                    "desired billboard location, duration of campaign example: 1 month up to 12 month period "
                    "get the estimated budget if they have one "
                    "confirm the phone number and email before ending the call "
                    "dont use markdown because your response is spoken aloud "
                    ),
                ),
            )

    # the mouth of the operation tts
    tts = OpenAITTSService(
            api_key = api_key,
            settings = OpenAITTSService.Settings(
                model = "gpt-4o-mini-tts",
                voice = "coral",
                instructions = (
                    "speak warmly and professionally at a moderate pace "
                    "sound like a helpful sales coordinator"
                    ),
                ),
            )

    # store the conversion passed to the LLM
    context = LLMContext()

    # silero detects when the caller starts and stops speaking
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params = LLMUserAggregatorParams(
                vad_analyzer = SileroVADAnalyzer(),
                ),
            )

    pipeline = Pipeline(
            [
                transport.input(),
                stt,
                user_aggregator,
                llm,
                tts,
                transport.output(),
                assistant_aggregator,
            ]
        )

    worker = PipelineWorker(
            pipeline,
            params = PipelineParams(
                audio_in_sample_rate=8000,
                audio_out_sample_rate=8000,
                enable_metrics = True,
                enable_usage_metrics = True,
                ),
            idle_timeout_secs = runner_args.pipeline_idle_timeout_secs,
            )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client) -> None:
        logger.info("Twilio caller connected")

        # add an instruction that causes the bot to speak first.
        context.add_message(
                {
                    "role": "developer",
                    "content" : (
                        "Greet the caller, explain that you are a AI voicemail agent and can help collect client info and a human will contact you during normal business hours"
                        )
                }
            )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnect")
    async def on_client_disconnect(transport, client) -> None:
        logger.info("Twilio caller disconnected")
        await worker.cancel()

    worker_runner = WorkerRunner(
            handle_sigint = runner_args.handle_sigint,
            force_gc = True,
            )

    await worker_runner.add_workers(worker)
    await worker_runner.run()

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


