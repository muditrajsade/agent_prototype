import asyncio

from livekit import api 
from livekit.protocol.sip import CreateSIPParticipantRequest, SIPParticipantInfo
import os
from dotenv import load_dotenv

load_dotenv(".env.local")


livekit_url = os.getenv("LIVEKIT_URL")
livekit_api_key = os.getenv("LIVEKIT_API_KEY")
livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")


async def main():
    livekit_api = api.LiveKitAPI(livekit_url, livekit_api_key, livekit_api_secret)

    request = CreateSIPParticipantRequest(
        sip_trunk_id = "ST_HRTGNnnP2rbu",
        sip_call_to = "+919108806409",
        room_name = "outbound-919108806409",
        participant_identity = "sip-test",
        participant_name = "Test Caller",
        krisp_enabled = True,
        wait_until_answered = True
    )
    
    try:
        participant = await livekit_api.sip.create_sip_participant(request)
        print(f"Successfully created SIP participant: {participant}")

        # FIX 1: Use livekit_api.agent_dispatch and api.CreateAgentDispatchRequest
        await livekit_api.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="go agent", 
                room="outbound-919108806409",
            )
        )
        print("Agent successfully dispatched to the room!")
        
    except Exception as e:
        print(f"Error: {e}")
        # FIX 2: Safely check if the error has metadata before trying to print it
        if hasattr(e, "metadata") and e.metadata is not None:
            print(f"SIP error code: {e.metadata.get('sip_status_code')}")
            print(f"SIP error message: {e.metadata.get('sip_status')}")
    finally:
        await livekit_api.aclose()

asyncio.run(main())