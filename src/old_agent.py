import asyncio
import logging
from dotenv import load_dotenv
from pydub import AudioSegment
import aiohttp

from livekit.agents import JobContext, AgentServer, cli, Agent, AgentSession, function_tool
from livekit.plugins import openai, deepgram, sarvam, silero
from livekit import api, rtc

logger = logging.getLogger("agent")
load_dotenv(".env.local")

server = AgentServer()

# --- HELPER FUNCTION TO PLAY MP3 AUDIO ---
async def play_audio_file(room: rtc.Room, file_path: str):
    """
    Loads any audio file (MP3, WAV, etc.), converts it to 16-bit PCM on the fly, 
    and streams it directly to the LiveKit room.
    """
    logger.info(f"Loading audio file: {file_path}")
    # 1. Load the audio file (pydub auto-detects format)
    audio = AudioSegment.from_file(file_path)
    
    # 2. Force conversion to 16-bit PCM for LiveKit compatibility
    audio = audio.set_sample_width(2)
    
    sample_rate = audio.frame_rate
    num_channels = audio.channels
    
    # 3. Create the LiveKit audio source and track
    source = rtc.AudioSource(sample_rate, num_channels)
    track = rtc.LocalAudioTrack.create_audio_track("prerecorded_intro", source)
    options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    
    await room.local_participant.publish_track(track, options)
    
    # 4. Stream audio in 20ms chunks
    chunk_ms = 20
    
    try:
        # pydub allows us to slice the audio directly by milliseconds
        for i in range(0, len(audio), chunk_ms):
            chunk = audio[i:i+chunk_ms]
            data = chunk.raw_data
            
            # Skip empty chunks at the very end
            if not data:
                break
                
            audio_frame = rtc.AudioFrame(
                data=data,
                sample_rate=sample_rate,
                num_channels=num_channels,
                samples_per_channel=len(data) // (2 * num_channels)
            )
            source.capture_frame(audio_frame)
            
            # Sleep to match real-time playback speed
            await asyncio.sleep(chunk_ms / 1000.0)
            
    finally:
        # Flush final frames and unpublish the pre-recorded track
        await asyncio.sleep(0.5) 
        await room.local_participant.unpublish_track(track.sid)
# -----------------------------------------


@server.rtc_session(agent_name="real-estate-agent")
async def my_agent(ctx: JobContext):
    await ctx.connect()
    
    call_state = {
        "interested": False,
        "whatsapp_sent": False
    }

    async def send_whatsapp_payload():
        if call_state["whatsapp_sent"]:
            return

        phone_number = ctx.room.name.replace("outbound-", "")
        interest_status = "Interested" if call_state["interested"] else "Not Interested"
        print(f"⚠️ SENDING WHATSAPP LEAD: {phone_number} | Project: Godrej Bannerghatta Road | Status: {interest_status}")

        url = "https://api.sandeshai.com/whatsapp/campaign/api/"
        template_vars = [
            phone_number,                  # {{1}} customer number
            "Customer",                    # {{2}} name
            "Godrej Bannerghatta Road",    # {{3}} project
            interest_status               # {{4}} status
        ]

        payload = {
            "apiKey": "2f7b07d9-8f50-4076-a0c4-2da04a37989b",
            "campaignName": "voice_api_camp",
            "whatsappNumber": "919032812294",
            "contactName": "Mudit",
            "templateVariables": template_vars
        }

        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(url, json=payload) as response:
                    response_data = await response.json()
                    print(f"✅ WhatsApp alert sent to team! Response: {response_data}")
        except Exception as e:
            logger.error(f"❌ WhatsApp API Error: {e}")

        call_state["whatsapp_sent"] = True
        
    
    # 1. Define your Strict Local Glossary as a single list
    local_glossary = [
        # BUILDERS
        "Lodha", "Mantri", "Valmark", "Assetz", "Salarpuria Sattva", "Puravankara", 
        "Sobha", "SNN Raj", "Vaswani", "Chaithanya", "Capstone Life", "Raheja", 
        "Radiance", "CNTC", "RRBC",
        
        # LOCATIONS
        "Bannerghatta", "Akshayanagar", "Hulimavu", "Singasandra", "Begur", 
        "Kanakapura", "Varthur", "Budigere", "Hennur", "Avalahalli", "Sahakarnagar", 
        "Hebbal", "Yelahanka", "Manyata", "Jakkur", "Doddaballapura", "Devanahalli", 
        "Koramangala", "Rajajinagar", "Yeshwanthpur", "Marathahalli", "Panathur", 
        "IVC Road", "Bellandur", "Sarjapur", "ITPL",
        
        # PROJECTS
        "Azur", "Apas", "Shibui", "Viviente", "Kessaku", "Neopolis", "Oakshire", 
        "Galera", "Vivarea", "Mirabelle", "Sankhya", "Canvas and Cove", "Aqua Vista", 
        "The Promont", "Park Hill", "Raintree Park", "White Meadows", "Radical Rhapsody", 
        "Mid Summer Rain", "Spencer Heights", "Built Rare", "Crystal Meadows",
        
        # JARGON
        "RTMI", "Villament", "BHK", "RERA", "Floor Plan","Crores", "Crore", "Cr", "Lakhs", "Lakh"
    ]

    # Initialize Individual Plugins
    my_vad = silero.VAD.load(
        activation_threshold=0.6,     
        min_speech_duration=0.1,      
        min_silence_duration=0.5,     
        prefix_padding_duration=0.1 
    )
    my_stt = deepgram.STT(
        model="nova-3",           
        language="en-IN",         
        keyterm=local_glossary    
    )

    # INITIALIZE CEREBRAS VIA OPENAI PLUGIN
    my_llm = openai.LLM.with_cerebras(model="gpt-oss-120b")
    
    my_tts = sarvam.TTS(
        target_language_code="en-IN",
        model="bulbul:v3", 
        speaker="ishita",
    )
    
    # This text should exactly match the words spoken in your greeting.mp3
    greeting_text = "Hello! I am calling from Godrej Properties. Godrej is launching a new project in Bannerghatta Road. Would you be interested in this pre-launch?"

    # 2. Initialize the Session with Pipeline Components
    session = AgentSession(
        vad=my_vad,
        stt=my_stt,
        llm=my_llm,
        tts=my_tts
    )

    my_instructions = f"""
You are a professional telecaller from Godrej Properties. Keep responses short, polite, and conversational. YOU MUST ONLY SPEAK IN ENGLISH.

# CONVERSATION FLOW:
1. GREETING: You have already greeted the user with a pre-recorded message saying: "{greeting_text}". Wait for the user's response.
2. ASSESS INTEREST: Listen carefully to their response.
   - If they say "Yes", "Sure", "Okay", "Tell me more", "Interested", or anything positive or curious: IMMEDIATELY call the `end_call` tool with interested=True.
   - If they say "No", "Not interested", "Busy", "Don't call again", or anything negative: IMMEDIATELY call the `end_call` tool with interested=False.
   - If they are UNSURE or ask a question (e.g., "What project?", "Where exactly?", "What is the price?"): Give ONE brief answer (location: Bannerghatta Road, Bangalore; it is a pre-launch residential project by Godrej Properties), then ask again "Would you be interested?" Once they respond, call `end_call` with the appropriate value.
3. END CALL: After calling `end_call`, say the closing line it returns and stop speaking.

# CRITICAL RULES:
- DO NOT ask for any personal details like name, budget, or email.
- DO NOT pitch multiple things. This call is ONLY to qualify interest.
- NO MARKDOWN: Speak in plain, natural conversational English only.
- NEVER say you are an AI or a bot.
- Keep every response under 2 sentences.
"""
    
    # 3. Define the Agent Logic
    @function_tool
    async def end_call(interested: bool):
        """Call this immediately once the user has responded to the interest question. Set interested=True if they said yes or showed any interest, False if they declined."""

        call_state["interested"] = interested
        print(f"Interest captured: {interested}")

        async def _wrap_up_routine():
            await asyncio.sleep(5)
            try:
                print("Deleting room to end call...")
                lkapi = api.LiveKitAPI()
                await lkapi.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
                await lkapi.aclose()
                print("✅ Room deleted and call ended.")
            except Exception as e:
                logger.error(f"Error ending call: {e}")
            await send_whatsapp_payload()

        asyncio.create_task(_wrap_up_routine())

        if interested:
            return "Thank you! Our team will reach out to you shortly with more details. Have a great day!"
        else:
            return "No problem at all. Sorry for the disturbance. Have a great day!"
    
    agent = Agent(
        instructions=my_instructions,
        tools=[end_call]
    )
    
    disconnect_future = asyncio.Future()

    @ctx.room.on("disconnected")
    def on_disconnected(*args, **kwargs):
        if not disconnect_future.done():
            disconnect_future.set_result(None)
                
    # Start the session
    await session.start(room=ctx.room, agent=agent)
    logger.info("Agent started and connected to the room...")

    # Wait shortly to ensure the WebRTC audio connection is fully stabilized
    await asyncio.sleep(0.5)
    
    logger.info("Triggering proactive greeting via pre-recorded MP3 file...")
    
    try:
        # Plays the MP3 file instead of generating TTS
        await play_audio_file(ctx.room, "greeting.mp3")
    except Exception as e:
        logger.error(f"Failed to play pre-recorded greeting: {e}")
    
    # The script waits here until the room disconnects 
    await disconnect_future

    # As soon as the call ends, fire the final Drop-Catch check
    await send_whatsapp_payload()

if __name__ == "__main__":
    cli.run_app(server)