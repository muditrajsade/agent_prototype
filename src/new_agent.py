import asyncio
import logging
import re
import os
from dotenv import load_dotenv
import typesense

# --- New v1.5.x Imports ---
from livekit.agents import JobContext, AgentServer, cli, Agent, AgentSession, function_tool, RunContext
from livekit.plugins import openai
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
# TOOL DEFINITION (v1.x style)
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
        
        # --- NEW FALLBACK LOGIC ---
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
            results_string = f"0 exact matches found, but found {len(fallback_hits)} fallback properties based on budget. YOU MUST TELL THE USER EXACTLY: 'With your preferences no property is available, instead I would recommend these:' Then pitch them ONE by ONE. Do not list them all at once:\n"
            for i, hit in enumerate(fallback_hits):
                doc = hit['document']
                results_string += f"Property {i+1}: {doc.get('project_name')} by {doc.get('builder')} in {doc.get('location')}. Price: {doc.get('starting_price_cr')} Cr. USP: {doc.get('usp')}\n"
            
            return results_string
        # --------------------------

        # Exact matches found
        results_string = f"Found {len(hits)} properties matching the exact criteria. Pitch them ONE by ONE. Do not list them all at once:\n"
        for i, hit in enumerate(hits):
            doc = hit['document']
            results_string += f"Property {i+1}: {doc.get('project_name')} by {doc.get('builder')} in {doc.get('location')}. Price: {doc.get('starting_price_cr')} Cr. USP: {doc.get('usp')}\n"
        
        return results_string

    except Exception as e:
        print(f"Database Error: {e}")
        return "There was a database error. Apologize to the user and tell them you are having system issues."

# ==========================================
# SERVER SETUP
# ==========================================
server = AgentServer()

@server.rtc_session(agent_name="real-estate-agent")
async def my_agent(ctx: JobContext):
    await ctx.connect()

    # 1. Initialize the new unified AgentSession with the Realtime API
    # 1. Initialize the new unified AgentSession with the Realtime API
    # 1. Initialize the new unified AgentSession with the Realtime API
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime-mini",
            voice="shimmer",
            turn_detection=TurnDetection(
                type="server_vad",
                threshold=0.9,             
                prefix_padding_ms=300, 
                silence_duration_ms=400,
                create_response=True,
                interrupt_response=True
            )
        )
    )

    # 2. Define your agent's brain and attach tools using the new Agent class
    my_instructions = """
    You are a professional real estate agent from Property First. Keep responses conversational and concise. YOU MUST ONLY SPEAK IN ENGLISH.

# CONVERSATION FLOW:
Your goal is to guide the user through a natural conversation:
1. GREETING: Wait for the user to speak first. When they do, immediately introduce yourself.
2. GATHER PREFERENCES: Ask for their preferred Location, Budget, Builder, and Project. 
   - SPECIAL HANDLING for "ANY": 
     * If the user says they are "open to any" location (or similar), set the Location parameter to "any".
     * If the user says they are "open to any" budget (or similar), set the Budget parameter to "10 cr".
3. SEARCH: Once you have all 4 preferences (even if some are "any"), call the `search_properties` tool.
4. PITCHING: Pitch the returned properties ONE AT A TIME. Mention the Project Name, Location, Price, and 1 USP. After pitching one, ask if they want to hear the next one.
5. BROCHURE: Ask if they want the brochure. If yes, tell them you will send it to their WhatsApp and say goodbye.

# STRICT LOCAL GLOSSARY (CRITICAL FOR INDIAN ACCENTS):
You must strictly use the exact spellings below whenever you hear anything that sounds phonetically similar:
- BUILDERS: Lodha, Mantri, Valmark, Assetz, Salarpuria Sattva, Puravankara, Sobha, SNN Raj, Vaswani, Chaithanya, Capstone Life, Raheja, Radiance, CNTC, RRBC.
- LOCATIONS: Bannerghatta, Akshayanagar, Hulimavu, Singasandra, Begur, Kanakapura, Varthur, Budigere, Hennur, Avalahalli, Sahakarnagar, Hebbal, Yelahanka, Manyata, Jakkur, Doddaballapura, Devanahalli, Koramangala, Rajajinagar, Yeshwanthpur, Marathahalli, Panathur, IVC Road, Bellandur, Sarjapur, ITPL.
- PROJECTS: Azur, Apas, Shibui, Viviente, Kessaku, Neopolis, Oakshire, Galera, Vivarea, Mirabelle, Sankhya, Canvas and Cove, Aqua Vista, The Promont, Park Hill, Raintree Park, White Meadows, Radical Rhapsody, Mid Summer Rain, Spencer Heights, Built Rare, Crystal Meadows.
- JARGON: RTMI (Ready to Move In), Villament, BHK , RERA , Floor Plan.

# CRITICAL RULES:
- THE INITIAL GREETING: When the user says hello, do not use filler words like "Great" or "Hi there". Immediately respond with the exact phrase: "Hello! I am a voice agent from Property First. Are you looking to invest in real estate today?"
- NEVER leave the user in silence. When you have gathered their preferences, you MUST say something like, "Give me just a second to check our database for those exact requirements..." BEFORE you call the `search_properties` tool.
- DEEP DETAILS & INTERRUPTIONS: If the user interrupts to ask for specific details not provided by the search (like RERA number, floor plans, dimensions, or number of rooms), DO NOT guess or hallucinate. Immediately answer with: "All this information is available in the brochure, would you like to receive it?"
- EXHAUSTED LIST: The search tool will return a list of available properties. Keep track of how many you have pitched. Once you have pitched every single property on that list and the user asks for more, DO NOT call the search tool again. You MUST say exactly: "Sorry, only these properties match your requirements. So if you want more, would you like to alter your location, budget, builder, or project name preferences?"
- SPELLING MATCH: Always use the exact spellings from the glossary when calling the `search_properties` tool.
- NO HALLUCINATIONS: Only pitch exactly what the `search_properties` tool returns.
- NO MARKDOWN: Because you are a voice agent, do not use bullet points, asterisks, or bold text in your internal monologue or output. Speak naturally in plain text.
- FALLBACK PITCH: If the search tool tells you that it found fallback properties instead of exact matches, you MUST introduce the first property by saying exactly: "With your preferences no property is available, instead I would recommend these." Do not apologize, just state it confidently.
"""

    agent = Agent(
        instructions=my_instructions,
        tools=[search_properties]
    )

    # 3. Start the session
    # 3. Start the session
    await session.start(
        room=ctx.room,
        agent=agent
    )
    
    
    logger.info("Agent started and waiting for user to speak...")

if __name__ == "__main__":
    cli.run_app(server)