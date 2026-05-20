

import logging
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
    room_io,
    function_tool,
     RunContext
)
import re
from livekit.plugins import ai_coustics, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins import openai , deepgram, sarvam, silero
import typesense
import os
import asyncio
logger = logging.getLogger("agent")

load_dotenv(".env.local")

ts_client = typesense.Client({
  'nodes': [{'host': 'qjwn6bo8plhax0rdp-1.a2.typesense.net', 'port': '443', 'protocol': 'https'}],
  'api_key': os.getenv("TYPESENSE_API_KEY", "083TWWQruebrJtdpvaPMB1kv7DW60Pui"), 
  'connection_timeout_seconds': 2
})


@function_tool
async def search_properties(context: RunContext, location: str, budget: str, builder: str = "any", project: str = "any"):
    """Searches the database for properties. Call this ONLY after gathering Location, Budget, Builder, and Project."""
    print(f"Executing Search: Loc:{location}, Budg:{budget}, Build:{builder}, Proj:{project}")
    
    search_terms = [t for t in [location, builder, project] if t and t.lower() != "any"]
    query_string = " ".join(search_terms) if search_terms else "*"

    search_parameters = {
        'q': query_string,
        'query_by': 'location,builder,project_name,region', 
        'per_page': 20 
    }

    max_budget = None
    filter_string = None
    if budget and budget.lower() != "any":
        match = re.search(r"(\d+(\.\d+)?)", budget)
        if match:
            max_budget = float(match.group(1)) * 1.1 
            filter_string = f'starting_price_cr:<={max_budget}'
            search_parameters['filter_by'] = filter_string
            search_parameters['sort_by'] = 'starting_price_cr:desc' 

    try:
        search_results = ts_client.collections['real_estate_projects'].documents.search(search_parameters)
        hits = search_results.get('hits', [])
        
        # --- FALLBACK LOGIC ---
        if not hits:
            print("No exact matches found. Attempting fallback search by budget only...")
            fallback_params = {
                'q': '*',
                'per_page': 20
            }
            if filter_string:
                fallback_params['filter_by'] = filter_string
                fallback_params['sort_by'] = 'starting_price_cr:desc'
                
            fallback_results = ts_client.collections['real_estate_projects'].documents.search(fallback_params)
            fallback_hits = fallback_results.get('hits', [])
            
            if not fallback_hits:
                return "0 properties found even with the fallback budget search. Tell the user you couldn't find any matches at all and ask if they are open to changing their budget."
            
            # We found fallback properties!
            results_string = f"0 exact matches found, but found {len(fallback_hits)} fallback properties based on budget. YOU MUST TELL THE USER EXACTLY: 'With your preferences no property is available, instead I would recommend these:' Then pitch them STRICTLY ONE BY ONE. After pitching one, immediately ask if they want the brochure. Do not list them all at once:\n"
            for i, hit in enumerate(fallback_hits):
                doc = hit['document']
                results_string += f"Property {i+1}: {doc.get('project_name')} by {doc.get('builder')} in {doc.get('location')}. Price: {doc.get('starting_price_cr')} Cr. USP: {doc.get('usp')}\n"
            
            return results_string

        # Exact matches found
        results_string = f"Found {len(hits)} properties matching the exact criteria. Pitch them STRICTLY ONE BY ONE. After pitching one, immediately ask if they want the brochure for this property. Do not list them all at once:\n"
        for i, hit in enumerate(hits):
            doc = hit['document']
            results_string += f"Property {i+1}: {doc.get('project_name')} by {doc.get('builder')} in {doc.get('location')}. Price: {doc.get('starting_price_cr')} Cr. USP: {doc.get('usp')}\n"
        
        return results_string

    except Exception as e:
        print(f"Database Error: {e}")
        return "There was a database error. Apologize to the user and tell them you are having system issues."





server = AgentServer()

@server.rtc_session(agent_name="real-estate-agent")
async def my_agent(ctx: JobContext):
    await ctx.connect()
    
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

# 2. Update your Deepgram STT initialization

    # 1. Initialize Individual Plugins
    my_vad = silero.VAD.load(
        activation_threshold=0.4,
        min_speech_duration=0.15,
        min_silence_duration=0.4,
        prefix_padding_duration=0.1  # <-- Changed from padding_duration
    )
    my_stt = deepgram.STT(
    model="nova-3",           # LiveKit's default STT model
    language="en-IN",         # Sets the dialect to Indian English
    keyterm=local_glossary    # Passes the custom vocabulary to boost recognition
)

    my_llm = openai.LLM.with_ollama(
        model="qwen2.5:3b",
        base_url="http://localhost:11434/v1",
    )
    my_tts = sarvam.TTS(
        target_language_code="en-IN",
        model="bulbul:v3", 
        speaker="ishita",
    )
    
    greeting_text = "Hello! I am from Property First. Are you looking to invest in real estate in Bangalore?"
    # We call synthesize() now so Sarvam generates the audio in the background
    # BEFORE the session even starts.
    #greeting_audio = my_tts.synthesize(greeting_text)
    

    # 2. Initialize the Session with Pipeline Components
    session = AgentSession(
        vad=my_vad,
        stt=my_stt,
        llm=my_llm,
        tts=my_tts
    )

    my_instructions = """
You are a professional real estate agent for Property First in Bangalore. Keep responses brief, conversational, and in English.

YOUR STRICT STEP-BY-STEP MISSION:

STEP 1: GREETING
- Start the conversation EXACTLY with: "Hello! I am from Property First. Are you looking to invest in real estate in Bangalore?"
- Wait for the user to answer.

STEP 2: GATHER MANDATORY INFORMATION
You MUST collect TWO mandatory pieces of information before searching:
1. Location (e.g., Hebbal, Sarjapur)
2. Maximum Budget (e.g., 2 Crores, 80 Lakhs)
- If the user only gives a location, you MUST ask: "What is your maximum budget?"
- If the user only gives a budget, you MUST ask: "Which location are you looking at?"
- DO NOT call the search tool until you have BOTH the location and the budget.

STEP 3: GATHER OPTIONAL INFORMATION
- Builder (default is "any")
- Project Name (default is "any")
- Only ask about these if it flows naturally, but you do not strictly need them to search.

STEP 4: SEARCH
- ONLY WHEN you have both a Location and a Budget, say EXACTLY: "Give me just a second to check our database for those exact requirements."
- Immediately call the `search_properties` tool.

STEP 5: PITCHING
- Pitch the properties returned by the tool STRICTLY ONE AT A TIME.
- Say the Project Name, Location, Price, and 1 USP.
- Immediately ask: "Would you like to receive the brochure for this property?"
- If YES: Say "I will send that brochure to you shortly. Thank you for choosing Property First, goodbye."
- If NO: Pitch the next property on the list.

CRITICAL RULES:
- NEVER guess or make up a property.
- NEVER search with an empty budget.
- All numbers are in Crores (Cr). "2" = "2 Cr". "75 lakhs" = "0.75 Cr".
- If the user asks for specific details (floor plans, RERA), reply: "You can receive all those details in the brochure, would you like to receive that brochure?"
"""
    # 3. Define the Agent Logic
    agent = Agent(
        instructions=my_instructions,
        tools=[search_properties]
    )
    
    # Start the session
    await session.start(room=ctx.room, agent=agent)
    logger.info("Agent started and connected to the room...")

    # Wait 1.5 seconds to ensure the WebRTC audio connection is fully stabilized
    await asyncio.sleep(0.2)
    
    logger.info("Triggering proactive greeting...")
    
    # In LiveKit v1.5+, this is the correct way to make the agent speak first
    await session.say(
        text=greeting_text,
        #audio=greeting_audio,  # This skips the TTS generation step entirely!
        add_to_chat_ctx=True
    )

if __name__ == "__main__":
    cli.run_app(server)



