import asyncio
import logging
import re
import os
from dotenv import load_dotenv
import typesense

# --- Imports for v1.x ---
# 1. Swapped sarvam for elevenlabs
from livekit.agents import JobContext, AgentServer, cli, Agent, AgentSession, function_tool, RunContext
from livekit.plugins import openai, elevenlabs 
from openai.types.beta.realtime.session import TurnDetection

logger = logging.getLogger("agent")
load_dotenv(".env.local")

# ==========================================
# DATABASE SETUP
# ==========================================
ts_client = typesense.Client({
    'nodes': [{'host': 'qjwn6bo8plhax0rdp-1.a2.typesense.net', 'port': '443', 'protocol': 'https'}],
    'api_key': os.getenv("TYPESENSE_API_KEY", "083TWWQruebrJtdpvaPMB1kv7DW60Pui"), 
    'connection_timeout_seconds': 2
})

# ==========================================
# TOOL DEFINITION
# ==========================================
@function_tool
async def search_properties(context: RunContext, location: str, budget: str, builder: str = "any", project: str = "any"):
    """Searches the database for properties. Call this ONLY after gathering Location, Budget, Builder, and Project."""
    print(f"Executing Search: Loc:{location}, Budg:{budget}, Build:{builder}, Proj:{project}")
    
    search_terms = [t for t in [location, builder, project] if t and t.lower() != "any"]
    query_string = " ".join(search_terms) if search_terms else "*"

    search_parameters = {
        'q': query_string,
        'query_by': 'location,builder,project_name,region', 
        'per_page': 2 
    }

    if budget and budget.lower() != "any":
        match = re.search(r"(\d+(\.\d+)?)", budget)
        if match:
            max_budget = float(match.group(1)) * 1.1 
            search_parameters['filter_by'] = f'starting_price_cr:<={max_budget}'
            search_parameters['sort_by'] = 'starting_price_cr:desc' 

    try:
        search_results = ts_client.collections['real_estate_projects'].documents.search(search_parameters)
        hits = search_results.get('hits', [])
        
        if not hits:
            return "0 properties found. Tell the user you couldn't find exact matches and ask if they are open to changing their location or budget."

        results_string = "Here are the properties found. Pitch them ONE by ONE immediately without waiting for the user to speak:\n"
        for i, hit in enumerate(hits):
            doc = hit['document']
            results_string += f"Property {i+1}: {doc.get('project_name')} by {doc.get('builder')} in {doc.get('location')}. Price: {doc.get('starting_price_cr')} Cr. USP: {doc.get('usp')}\n"
        
        return results_string

    except Exception as e:
        logger.error(f"Database Error: {e}")
        return "There was a database error. Apologize to the user and tell them you are having system issues."

# ==========================================
# SERVER SETUP
# ==========================================
server = AgentServer()

@server.rtc_session(agent_name="real-estate-agent")
async def my_agent(ctx: JobContext):
    await ctx.connect()

    # 1. Initialize Session with OpenAI Realtime for Brain & ElevenLabs for Voice
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime-mini",
            modalities=["text"], 
            turn_detection=TurnDetection(
                type="server_vad",
                threshold=0.5,              
                prefix_padding_ms=200, 
                silence_duration_ms=250, # Tightened slightly for faster response
                create_response=True,
                interrupt_response=True
            )
        ),
        # 2. Attach ElevenLabs TTS here
        tts=elevenlabs.TTS(
            model="eleven_turbo_v2_5", 
            voice_id="zEoa2AiakhTnKPSlAhoX" # Just pass the ID string directly!
        )
    )

    # 2. Define instructions and tool-set
    my_instructions = """
You are a professional real estate agent from Property First. Keep responses conversational and concise. YOU MUST ONLY SPEAK IN ENGLISH.

# SPEAKING STYLE & LATENCY OPTIMIZATION (CRITICAL):
You are a voice agent driving a text-to-speech engine. You must speak in extremely short, punchy sentences. 
- ALWAYS begin every single response with a very short filler sentence of 2 to 4 words. (e.g., "Got it.", "Sure thing.", "Let me check.", "Okay, great."). This allows the voice engine to start speaking instantly.
- NEVER use long, run-on sentences. 
- ALWAYS break up your thoughts using periods (.) and commas (,).
- NO MARKDOWN: Do not use bullet points, asterisks, or bold text, as it disrupts the speech engine. Speak naturally in plain text.

# CONVERSATION FLOW:
Your goal is to guide the user through a natural conversation:
1. GREETING: Introduce yourself and ask if they are looking to invest in real estate. 
2. GATHER PREFERENCES: If they say yes, ask for their preferred Location, Budget, Builder, and Project. 
3. SEARCH: Once you have all 4 preferences, call the `search_properties` tool.
4. PITCHING: Pitch the returned properties ONE AT A TIME. Mention the Project Name, Location, Price, and 1 USP. 
5. BROCHURE: Ask if they want the brochure. If yes, tell them you will send it to their WhatsApp and say goodbye.

# STRICT LOCAL GLOSSARY (CRITICAL FOR INDIAN ACCENTS):
You must strictly use the exact spellings below whenever you hear anything that sounds phonetically similar:
- BUILDERS: Lodha, Mantri, Valmark, Assetz, Salarpuria Sattva, Puravankara, Sobha, SNN Raj, Vaswani, Chaithanya, Capstone Life, Raheja, Radiance, CNTC, RRBC.
- LOCATIONS: Bannerghatta, Akshayanagar, Hulimavu, Singasandra, Begur, Kanakapura, Varthur, Budigere, Hennur, Avalahalli, Sahakarnagar, Hebbal, Yelahanka, Manyata, Jakkur, Doddaballapura, Devanahalli, Koramangala, Rajajinagar, Yeshwanthpur, Marathahalli, Panathur, IVC Road, Bellandur, Sarjapur.
- PROJECTS: Azur, Apas, Shibui, Viviente, Kessaku, Neopolis, Oakshire, Galera, Vivarea, Mirabelle, Sankhya, Canvas and Cove, Aqua Vista, The Promont, Park Hill, Raintree Park, White Meadows, Radical Rhapsody, Mid Summer Rain, Spencer Heights, Built Rare, Crystal Meadows.
- JARGON: RTMI (Ready to Move In), Villament, BHK.

# CRITICAL RULES:
- NEVER leave the user in silence. When you have gathered their preferences, you MUST say something like, "Give me just a second to check our database." BEFORE you call the `search_properties` tool.
- SPELLING MATCH: Always use the exact spellings from the glossary when calling the `search_properties` tool. Never pass hallucinated names like "Baner" instead of "Bannerghatta".
- NO HALLUCINATIONS: Only pitch exactly what the `search_properties` tool returns.
"""
    agent = Agent(
        instructions=my_instructions,
        tools=[search_properties]
    )

    # 3. Start the session
    await session.start(
        room=ctx.room,
        agent=agent
    )

    # 4. Opening greeting 
    await session.say(
        "Hi there! This is Property First. Are you looking to invest in real estate today?", 
        allow_interruptions=False
    )

if __name__ == "__main__":
    cli.run_app(server)