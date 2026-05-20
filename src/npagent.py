import asyncio

import logging

import aiohttp

import time

from dotenv import load_dotenv

from pydub import AudioSegment



from livekit.agents import JobContext, AgentServer, cli, Agent, AgentSession, function_tool

from livekit.plugins import openai, deepgram, silero, sarvam

from livekit import api, rtc



logger = logging.getLogger("agent")

load_dotenv(".env.local")



server = AgentServer()



# --- 1. OPTIMIZED AUDIO PLAYER (NATIVE BUFFERING) ---

async def play_audio_file(room: rtc.Room, file_path: str):

    """Natively streams audio to eliminate jitter and reduces startup latency."""

    logger.info(f"Streaming: {file_path}")

    try:

        audio = AudioSegment.from_file(file_path)

        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)

       

        source = rtc.AudioSource(audio.frame_rate, audio.channels)

        track = rtc.LocalAudioTrack.create_audio_track("playback", source)

        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)

       

        publication = await room.local_participant.publish_track(track, options)

       

        chunk_ms = 20

        samples_per_chunk = int(audio.frame_rate * (chunk_ms / 1000.0))

        bytes_per_chunk = samples_per_chunk * 2 * audio.channels

        raw_data = audio.raw_data

       

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

            await asyncio.sleep(0.015) # Feed buffer faster than real-time for stability

           

    finally:

        await asyncio.sleep(0.8)

        try:

            await room.local_participant.unpublish_track(publication.sid)

        except:

            pass



# --- 2. MAIN AGENT LOGIC ---

@server.rtc_session(agent_name="real-estate-agent")

async def my_agent(ctx: JobContext):

    await ctx.connect()

   

    call_state = {"status": "Not Interested", "whatsapp_sent": False}



    async def send_whatsapp_payload():

        if call_state["whatsapp_sent"]: return

        phone_number = ctx.room.name.replace("outbound-", "")

       

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

    async def process_interest(interested: bool):

        """Call this for direct Yes/No responses or Brochure requests."""

        # IMMEDIATELY close the session so the AI stops listening

        await session.aclose()

       

        call_state["status"] = "Interested" if interested else "Not Interested"

        try:

            await play_audio_file(ctx.room, "ii.wav")

        finally:

            asyncio.create_task(hang_up_routine())

        return "SUCCESS"



    # --- CONSOLIDATED TOOL ---

    # --- 1. DEFINE THE TOOL (INSTANT RETURN) ---

    @function_tool

    async def process_call_outcome(outcome_type: str):

        """

        Call this to record the result and play the closing audio.

        outcome_type options: 'interested', 'not_interested', 'valid_question', 'rubbish_question'

        """

        call_state["pending_outcome"] = outcome_type

        return "SUCCESS"



    # --- 2. ADD AN OUTCOME HANDLER ---

    async def handle_outcome_audio(outcome_type: str):

        # Stop the AI ears

        await session.aclose()

       

        if outcome_type == 'direct_interest':

            call_state["status"] = "Interested"

            audio_to_play = "ii.wav"

        elif outcome_type == 'question':

            call_state["status"] = "Interested - Asked Questions"

            audio_to_play = "rr.wav"

        else:

            call_state["status"] = "Not Interested"

            audio_to_play = "rr.wav"

       

        try:

            await play_audio_file(ctx.room, audio_to_play)

        finally:

            await hang_up_routine()



    # --- 3. THE "MONITOR" LOOP ---

    # This runs in the background and watches for the tool to set an outcome

    # --- 3. THE "MONITOR" LOOP ---

    # --- 3. THE "MONITOR" LOOP ---

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

                    audio_file = "ii.wav"

               

                elif outcome == 'not_interested':

                    call_state["status"] = "Not Interested"

                    audio_file = "ii.wav"

               

                elif outcome == 'valid_question':

                    call_state["status"] = "Interested"  # Marked as interested for asking valid questions

                    audio_file = "rr.wav"

               

                elif outcome == 'rubbish_question':

                    call_state["status"] = "Not Interested" # Marked as not interested for rubbish

                    audio_file = "rr.wav"

               

                else:

                    # Fallback just in case

                    call_state["status"] = "Not Interested"

                    audio_file = "ii.wav"



                # Start playback immediately

                try:

                    await play_audio_file(ctx.room, audio_file)

                finally:

                    await hang_up_routine()

                break

            await asyncio.sleep(0.01)

    # Start the monitor at the beginning of your session

    asyncio.create_task(outcome_monitor())



    my_instructions = """

    You are an AI qualifying leads for Godrej Bannerghatta Road.

   

    CRITICAL LOGIC:

    1. If user says "Yes", "Interested", "Send brochure", "Ok", or "Sure":

       Call `process_call_outcome(outcome_type='interested')`

       

    2. If user says "No" or "Not interested" to the brochure:

       Call `process_call_outcome(outcome_type='not_interested')`



    3. If user asks a VALID question about the real estate (price, location, project details):

       Call `process_call_outcome(outcome_type='valid_question')`



    4. If user asks a RUBBISH/unrelated question, or says nonsense:

       Call `process_call_outcome(outcome_type='rubbish_question')`



    DO NOT SPEAK. ONLY CALL THE TOOL ONCE.

    """



    # Update your agent initialization to use the new single tool

   

    # --- SESSION CONFIG ---

    fast_vad = silero.VAD.load(

        min_silence_duration=1.2,

        min_speech_duration=0.1,

        padding_duration=0.1

    )



    session = AgentSession(

        vad=fast_vad,

        stt=deepgram.STT(model="nova-3", language="en-IN"),

        llm=openai.LLM.with_cerebras(model="gpt-oss-120b",temperature=0), # Temp 0 = faster logic

        tts=sarvam.TTS(target_language_code="en-IN", speaker="anushka"),

    )

    agent = Agent(instructions=my_instructions, tools=[process_call_outcome])

    agent.audio_session = session



    # --- EXECUTION FLOW ---

    # 1. Wait for pickup

    user_answered = False

    while not user_answered:

        for p in ctx.room.remote_participants.values():

            if len(p.track_publications) > 0: user_answered = True

        if not user_answered: await asyncio.sleep(0.05)

           

    # 2. Play Intro FIRST (AI is not listening yet)

    try:

        await play_audio_file(ctx.room, "pp.wav")

    except Exception as e:

        logger.error(f"Error playing pp.wav: {e}")



    # 3. Start Agent Listener (AI only starts hearing AFTER intro finishes)

    await session.start(room=ctx.room, agent=agent)

   

    # Wait for disconnect

    disconnect_future = asyncio.Future()

    @ctx.room.on("disconnected")

    def on_disconnected(*args, **kwargs):

        if not disconnect_future.done(): disconnect_future.set_result(None)

   

    await disconnect_future

    await send_whatsapp_payload()



if __name__ == "__main__":

    cli.run_app(server)