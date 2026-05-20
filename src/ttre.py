import asyncio
import logging
import aiohttp
from dotenv import load_dotenv
from pydub import AudioSegment

from livekit.agents import JobContext, AgentServer, cli, Agent, AgentSession, function_tool
from livekit.plugins import openai, deepgram, silero
from livekit import api, rtc

logger = logging.getLogger("agent")
load_dotenv(".env.local")

server = AgentServer()

# --- 1. STRICT REAL-TIME AUDIO STREAMER (NO ECHO/JITTER) ---
# --- 1. STRICT REAL-TIME AUDIO STREAMER (NO ECHO/JITTER) ---
async def stream_audio_to_room(room: rtc.Room, file_path: str):
    """Natively streams audio perfectly synced to real-time clock to prevent buffer echo."""
    logger.info(f"Streaming: {file_path}")
    publication = None
    try:
        audio = AudioSegment.from_file(file_path)
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        
        source = rtc.AudioSource(audio.frame_rate, audio.channels)
        track = rtc.LocalAudioTrack.create_audio_track("playback", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        
        publication = await room.local_participant.publish_track(track, options)
        
        # INCREASED from 20ms to 50ms to drastically reduce Python event loop jitter
        chunk_ms = 50 
        samples_per_chunk = int(audio.frame_rate * (chunk_ms / 1000.0))
        bytes_per_chunk = samples_per_chunk * 2 * audio.channels
        raw_data = audio.raw_data
        
        loop = asyncio.get_event_loop()
        start_time = loop.time()
        chunk_index = 0
        
        for i in range(0, len(raw_data), bytes_per_chunk):
            chunk = raw_data[i:i + bytes_per_chunk]
            if not chunk: break
            if len(chunk) < bytes_per_chunk:
                chunk = chunk.ljust(bytes_per_chunk, b'\x00')

            audio_frame = rtc.AudioFrame(
                data=chunk,
                sample_rate=audio.frame_rate,
                num_channels=audio.channels,
                samples_per_channel=samples_per_chunk
            )
            await source.capture_frame(audio_frame)
            
            chunk_index += 1
            expected_time = start_time + (chunk_index * (chunk_ms / 1000.0))
            sleep_time = expected_time - loop.time()
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
                
    except asyncio.CancelledError:
        logger.info(f"Playback of {file_path} was gracefully interrupted.")
        raise
    finally:
        await asyncio.sleep(0.2)
        if publication:
            try:
                await room.local_participant.unpublish_track(publication.sid)
            except Exception:
                pass

# --- 2. MAIN AGENT LOGIC ---
@server.rtc_session(agent_name="go agent")
async def my_agent(ctx: JobContext):
    await ctx.connect()
    
    call_state = {
        "status": "Not Interested", 
        "whatsapp_sent": False, 
        "intro_played": False,
        "pending_outcome": None,
        "active_audio_task": None  
    }

    # --- AUDIO TASK MANAGER ---
    async def stop_audio():
        """Instantly kills whatever audio is currently playing."""
        if call_state.get("active_audio_task") and not call_state["active_audio_task"].done():
            logger.info("Interrupting currently playing audio...")
            call_state["active_audio_task"].cancel()
            try:
                await call_state["active_audio_task"]
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0.1) # Brief pause so LiveKit unpublishes cleanly

    async def play_managed_audio(file_path: str):
        """Ensures only ONE audio file plays at a time by cancelling the previous one."""
        await stop_audio()
            
        task = asyncio.create_task(stream_audio_to_room(ctx.room, file_path))
        call_state["active_audio_task"] = task
        
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def send_whatsapp_payload():
        if call_state["whatsapp_sent"]: return
        phone_number = ctx.room.name.split("-")[1] # Extracts phone number safely from new room format
        
        url = "https://api.sandeshai.com/whatsapp/campaign/api/"
        payload = {
            "apiKey": "2f7b07d9-8f50-4076-a0c4-2da04a37989b",
            "campaignName": "voice_api_camp",
            "whatsappNumber": "919032812294",
            "contactName": "Mudit",
            "templateVariables": [phone_number, "Customer", "Godrej Bannerghatta Road", call_state["status"]]
        }
        try:
            async with aiohttp.ClientSession() as http_session:
                await http_session.post(url, json=payload)
        except Exception as e:
            logger.error(f"WhatsApp Error: {e}")
        call_state["whatsapp_sent"] = True

    async def hang_up_routine():
        await asyncio.sleep(1.0)
        try:
            lkapi = api.LiveKitAPI()
            await lkapi.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
            await lkapi.aclose()
        except Exception as e:
            logger.warning(f"Hangup cleanup error: {e}")
        finally:
            await send_whatsapp_payload()

    # --- TOOLS ---
    @function_tool
    async def play_intro_audio():
        """
        Call this tool to play the main pitch. Use this for greetings, iPhone screening bots, OR if a human picks up after you were previously on hold.
        """
        # REMOVED the "ALREADY_PLAYED" lock so the bot can replay the pitch 
        # if an actual human interrupts after the iPhone bot's hold music.
        call_state["intro_played"] = True
        logger.info("Playing pp.wav (Intro Pitch)")
        
        await play_managed_audio("pp.wav")
        return "INTRO_PLAYED"

    @function_tool
    async def wait_on_hold():
        """
        Call this tool when the speaker's intent is to ask you to wait, pause, or stay on the line.
        """
        await stop_audio()
        logger.info("Bot heard an intent to wait/hold. Doing nothing and waiting...")
        return "WAITING"

    @function_tool
    async def process_call_outcome(outcome_type: str):
        """
        Call this ONLY when a human responds to your pitch to record their final intent, OR if a voicemail machine tells you to leave a message and hang up.
        outcome_type options: 'interested', 'not_interested', 'valid_question', 'rubbish_question', 'voicemail_hangup'
        """
        call_state["pending_outcome"] = outcome_type
        return "SUCCESS"

    # --- THE "MONITOR" LOOP ---
    async def outcome_monitor():
        while True:
            outcome = call_state.get("pending_outcome")
            if outcome:
                # Close the session in the background
                async def background_close():
                    try:
                        await session.aclose()
                    except Exception as e:
                        logger.warning(f"Error during background close: {e}")
                
                asyncio.create_task(background_close())

                # --- EXACT AUDIO & STATUS MAPPING ---
                if outcome == 'interested':
                    call_state["status"] = "Interested"
                    audio_file = "rr.wav" 
                
                elif outcome == 'not_interested':
                    call_state["status"] = "Not Interested"
                    audio_file = "ii.wav"
                
                elif outcome == 'valid_question':
                    call_state["status"] = "Interested" 
                    audio_file = "rr.wav"
                
                elif outcome == 'rubbish_question':
                    call_state["status"] = "Not Interested" 
                    audio_file = "rr.wav"

                elif outcome == 'voicemail_hangup':
                    call_state["status"] = "Voicemail Left" 
                    audio_file = "pp.wav" # Plays pitch and then automatically hangs up below
                
                else:
                    call_state["status"] = "Not Interested"
                    audio_file = "ii.wav"

                # Start playback immediately and hang up
                try:
                    await play_managed_audio(audio_file)
                finally:
                    await hang_up_routine()
                break
            await asyncio.sleep(0.01)

    asyncio.create_task(outcome_monitor())

    # --- FALLBACK TIMER ---
    async def intro_fallback_timer():
        # Wait 15.0 seconds to allow long voicemails or initial screening noise to pass
        await asyncio.sleep(15.0) 
        if not call_state["intro_played"] and not call_state.get("pending_outcome"):
            logger.info("Timeout reached. Firing pp.wav fallback.")
            call_state["intro_played"] = True
            await play_managed_audio("pp.wav")

    # --- INTENT-BASED PROMPT ---
    my_instructions = """
    You are an AI qualifying leads for Godrej Bannerghatta Road. You must analyze the semantic intent of the speaker (whether human or automated bot) and decide the correct action. Ignore background noise or slight transcript errors.
    
    CRITICAL WORKFLOW:
    
    1. THE OPENING (GREETINGS, VOICEMAILS & IPHONE SCREENING):
    - If the speaker greets normally (e.g., "Hello"): Call `play_intro_audio()`.
    - If an iPhone or automated bot says "reason for calling" or "I'll see if this person is available": Call `play_intro_audio()`. Do not assume they want a callback.
    - If an automated system says to leave a message and hang up: Call `process_call_outcome(outcome_type='voicemail_hangup')`.
    
    2. THE WAITING PHASE (HOLDING & INTERRUPTIONS):
    - If an automated system says "Thanks, stay on the line" or you hear hold music: Call `wait_on_hold()`.
    - IF a human suddenly interrupts with "Hello" AFTER you were put on hold by a screening bot, you MUST completely restart and call `play_intro_audio()` again to re-introduce yourself.

    3. HUMAN INTERACTION & QUALIFYING:
    - Intent is POSITIVE (interest, requesting details): Call `process_call_outcome(outcome_type='interested')`
    - Intent is NEGATIVE (refusal, "wrong number", not interested): Call `process_call_outcome(outcome_type='not_interested')`. MUST treat "wrong number" as not interested.
    - Intent is a RELEVANT QUESTION (property, price): Call `process_call_outcome(outcome_type='valid_question')`
    - Intent is IRRELEVANT (rubbish, gibberish): Call `process_call_outcome(outcome_type='rubbish_question')`

    DO NOT GENERATE ANY CONVERSATIONAL TEXT YOURSELF. ONLY CALL THE TOOLS based on the speaker's intent.
    """

    # --- SESSION CONFIG ---
    # --- SESSION CONFIG ---
    fast_vad = silero.VAD.load(
        min_silence_duration=0.7, 
        min_speech_duration=0.2,
        prefix_padding_duration=0.1  # <--- Changed from padding_duration to fix the warning
    )

    # Just pass the plain strings for Nova-3
    custom_keywords = [
        "Godrej", 
        "Bannerghatta", 
        "Bannerghatta Road",
        "brochure",
        "real estate",
        "property",
        "properties",
        "sales manager",
        "Not interested",
        "Interested",
    ]

    session = AgentSession(
        vad=fast_vad,
        stt=deepgram.STT(
            model="nova-3", 
            language="en-IN",
            keyterm=custom_keywords  
        ),
        llm=openai.LLM.with_cerebras(model="gpt-oss-120b", temperature=0), 
    )
    
    # CRITICAL: Prevent crash if LLM tries to talk without a TTS engine
    session.output.set_audio_enabled(False)
    
    agent = Agent(
        instructions=my_instructions, 
        tools=[play_intro_audio, wait_on_hold, process_call_outcome]
    )
    agent.audio_session = session

    # --- EXECUTION FLOW ---
    # --- EXECUTION FLOW ---
    
    # 1. START SESSION EARLY: Warm up STT and VAD *before* the user answers
    await session.start(room=ctx.room, agent=agent)

    # 2. WAIT FOR CONNECTION: Now wait for the callee's audio track to publish
    user_answered = False
    while not user_answered:
        for p in ctx.room.remote_participants.values():
            if len(p.track_publications) > 0: 
                user_answered = True
        if not user_answered: 
            await asyncio.sleep(0.05)
            
    # 3. START TIMERS: Trigger the fallback timer only after they connect
    asyncio.create_task(intro_fallback_timer())
    
    # 4. AWAIT DISCONNECT
    disconnect_future = asyncio.Future()
    @ctx.room.on("disconnected")
    def on_disconnected(*args, **kwargs):
        if not disconnect_future.done(): disconnect_future.set_result(None)
    
    await disconnect_future
    await send_whatsapp_payload()

if __name__ == "__main__":
    cli.run_app(server)