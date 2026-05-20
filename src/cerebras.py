import asyncio
import logging
import re
import os
from dotenv import load_dotenv
import itertools
import typesense
from livekit.agents import llm
# --- Modern v1.5.x Imports ---
from livekit.agents import JobContext, AgentServer, cli, Agent, AgentSession, function_tool, RunContext
# Changed from groq to openai
from livekit.plugins import openai, deepgram, sarvam, silero
from livekit.agents.llm import ChatContext

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
# TOOL DEFINITION (v1.5.x style)
# ==========================================
@function_tool
async def search_properties(context: RunContext, location: str, budget: str, builder: str = "any", project: str = "any"):
    """Searches the database for properties. Call this immediately once you have Location and Budget. Builder and Project are optional."""
    print(f"Executing Search: Loc:{location}, Budg:{budget}, Build:{builder}, Proj:{project}")
    
    # 1. Helper function to split "Bannerghatta or Hebbal" into ['Bannerghatta', 'Hebbal']
    def parse_options(param_str):
        if not param_str or param_str.lower() == "any":
            return ["any"]
        # Split by "or", "and", or commas
        options = [opt.strip() for opt in re.split(r'\b(?:or|and)\b|,', param_str, flags=re.IGNORECASE) if opt.strip()]
        return options if options else ["any"]

    locations = parse_options(location)
    builders = parse_options(builder)
    projects = parse_options(project)
    
    # 2. Generate all combinations (e.g., Loc1+Build1, Loc2+Build1, etc.)
    combinations = list(itertools.product(locations, builders, projects))
    print(f"Running searches for combinations: {combinations}")

    # 3. Parse budget once for all searches
    max_budget = None
    filter_string = None
    if budget and budget.lower() != "any":
        match = re.search(r"(\d+(\.\d+)?)", budget)
        if match:
            max_budget = float(match.group(1)) * 1.1 
            filter_string = f'starting_price_cr:<={max_budget}'

    all_hits = []
    seen_projects = set() # To prevent pitching duplicates from overlapping searches

    # 4. Run the combination search loop
    for loc, bld, proj in combinations:
        search_terms = [t for t in [loc, bld, proj] if t.lower() != "any"]
        query_string = " ".join(search_terms) if search_terms else "*"

        search_parameters = {
            'q': query_string,
            'query_by': 'location,builder,project_name,region', 
            'per_page': 20 
        }
        if filter_string:
            search_parameters['filter_by'] = filter_string
            search_parameters['sort_by'] = 'starting_price_cr:desc'

        try:
            search_results = ts_client.collections['real_estate_projects'].documents.search(search_parameters)
            hits = search_results.get('hits', [])
            
            for hit in hits:
                doc = hit['document']
                # Create a unique ID to ensure we don't add the same property twice
                unique_id = str(doc.get('project_name', '')) + str(doc.get('location', ''))
                if unique_id not in seen_projects:
                    seen_projects.add(unique_id)
                    all_hits.append(hit)
        except Exception as e:
            print(f"Database Error on combination {loc}-{bld}-{proj}: {e}")

    # 5. --- FALLBACK LOGIC --- (Only triggers if ALL combinations fail)
    if not all_hits:
        print("No exact matches found in any combination. Attempting fallback search by budget only...")
        fallback_params = {
            'q': '*',
            'per_page': 20
        }
        if filter_string:
            fallback_params['filter_by'] = filter_string
            fallback_params['sort_by'] = 'starting_price_cr:desc'
            
        try:
            fallback_results = ts_client.collections['real_estate_projects'].documents.search(fallback_params)
            fallback_hits = fallback_results.get('hits', [])
            
            if not fallback_hits:
                return "0 properties found even with the fallback budget search. Tell the user you couldn't find any matches at all and ask if they are open to changing their budget."
            
            results_string = f"0 exact matches found, but found {len(fallback_hits)} fallback properties based on budget. YOU MUST TELL THE USER EXACTLY: 'With your preferences no property is available, instead I would recommend these:' Then pitch them STRICTLY ONE BY ONE. After pitching one, immediately ask if they want the brochure. Do not list them all at once:\n"
            for i, hit in enumerate(fallback_hits):
                doc = hit['document']
                results_string += f"Property {i+1}: {doc.get('project_name')} by {doc.get('builder')} in {doc.get('location')}. Price: {doc.get('starting_price_cr')} Cr. USP: {doc.get('usp')}\n"
            
            return results_string
        except Exception as e:
            return "There was a database error. Apologize to the user and tell them you are having system issues."

    # 6. Exact matches found across combinations
    results_string = f"Found {len(all_hits)} properties matching the criteria. Pitch them STRICTLY ONE BY ONE. After pitching one, immediately ask if they want the brochure for this property. Do not list them all at once:\n"
    for i, hit in enumerate(all_hits):
        doc = hit['document']
        results_string += f"Property {i+1}: {doc.get('project_name')} by {doc.get('builder')} in {doc.get('location')}. Price: {doc.get('starting_price_cr')} Cr. USP: {doc.get('usp')}\n"
    
    return results_string

# ==========================================
# SERVER SETUP
# ==========================================
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

    # INITIALIZE CEREBRAS VIA OPENAI PLUGIN
    my_llm = openai.LLM.with_cerebras(model="llama3.1-8b")
    
    my_tts = sarvam.TTS(
        target_language_code="en-IN",
        model="bulbul:v3", 
        speaker="ishita",
    )
    
    greeting_text = "Hello! I am from Property First. Are you looking to invest in real estate in Bangalore?"

    # 2. Initialize the Session with Pipeline Components
    session = AgentSession(
        vad=my_vad,
        stt=my_stt,
        llm=my_llm,
        tts=my_tts
    )

    my_instructions = """
    You are a professional real estate agent from Property First. Keep responses conversational and concise. YOU MUST ONLY SPEAK IN ENGLISH.

# CONVERSATION FLOW (STRICT LOOP):
Your goal is to guide the user through a natural conversation:
1. GREETING: You will initiate the conversation. Do NOT wait for the user to speak.
2. GATHER PREFERENCES: Listen carefully. Extract Location and Budget. 
   - ONLY actively ask for Location and Budget. DO NOT ask for the Builder or Project Name.
   - If the user voluntarily mentions a Builder or Project, extract them for the search. Otherwise, default them to "any".
   - MULTIPLE OPTIONS: If the user provides multiple options (e.g., "Bannerghatta or Hebbal", "L&T or Brigade"), pass the exact phrase to the tool (e.g., location="Bannerghatta or Hebbal"). Do not force them to pick just one.
   - ONLY politely ask follow-up questions to gather missing Location or Budget parameters.
   - SPECIAL HANDLING: Open to "any" location = "any". Open to "any" budget = "10 cr".
3. SEARCH: Once you have the Location and Budget, say EXACTLY "Give me a minute let me check what suit your preferences the best...." then immediately call the `search_properties` tool.
   - CHIT-CHAT DURING SEARCH: If the user interrupts with a generic question while the search is running, reply EXACTLY "Give me a minute let me check what suit your preferences the best...."
   - CRITICAL: DO NOT repeat the wait phrase after the tool returns. Once the tool provides results, immediately start pitching without any filler words.
4. PITCHING LOOP: Pitch the returned properties ONE AT A TIME. 
   - Pitch the Project Name, Location, Price, and 1 USP.
   - Immediately ask: "Would you like to receive the brochure for this property?"
5. BROCHURE RESPONSES:
   - If YES: You MUST say EXACTLY: "I will send that brochure to you shortly. Thank you for choosing Property First, goodbye." (Do not pause, say this entire sentence at once).
   - If NO: Check your list from the tool. If there is another property, pitch the next one and ask about the brochure again.
6. EXHAUSTED LIST: If you have pitched all properties on the list (or if they say no to the final property's brochure), you MUST say EXACTLY: "These are all the properties that fit your preferences, would you like to alter your location, budget, builder, or project preferences?"
   - If YES to altering: Ask for their new preferences and start the gathering process over.
   - If NO to altering: Say EXACTLY "Thank you, it was nice assisting you, goodbye."

# CRITICAL RULES:
- LATE PREFERENCES (OVERRIDE): If the user interrupts a search or a pitch to ADD or CHANGE a preference (e.g., "Actually, I only want Brigade" or "Make the budget 5 Cr"), DO NOT pitch the current list. Immediately say "Let me update that and check again..." and call the `search_properties` tool AGAIN with the new, updated parameters.
- SKIP TO NEXT: If the user ever says "show me another property", "next", or "skip", immediately stop the current pitch, look at your list, move to the next property, and pitch it.
- DEEP DETAILS & INTERRUPTIONS: If the user asks for specific details (RERA, floor plans, dimensions, rooms), DO NOT guess or hallucinate. Reply EXACTLY: "You can receive all those details in the brochure, would you like to receive that brochure?"
- TRENDING PROPERTIES: If asked for 'hottest', 'trending', or 'best' without a budget, automatically assume '10 Cr' and search immediately.
- THE INITIAL GREETING: Your very first message MUST BE EXACTLY: "Hello! I am from Property First. Are you looking to invest in real estate in bangalore?"
- NO HALLUCINATIONS: Only pitch exactly what the `search_properties` tool returns. Do not make up properties.
- NO MARKDOWN: Speak naturally in plain text without bullet points or asterisks.
- FALLBACK PITCH: If the tool returns fallback properties, introduce the first one by saying EXACTLY: "With your preferences no property is available, instead I would recommend these." Do not apologize.
- BUDGET INTERPRETATION: All numbers are Crores (Cr). "2" = "2 Cr". "75 lakhs" = "0.75 Cr". Always pass budget with 'Cr' to the tool.
"""
    # 3. Define the Agent Logic
    agent = Agent(
        instructions=my_instructions,
        tools=[search_properties]
    )
    
    @session.on("agent_state_changed")
    def apply_sliding_window(ev):
        # Trigger the cleanup only when the agent finishes speaking and starts listening
        if "listening" in str(ev.new_state).lower():
            
            # 10 messages = 5 full conversational turns (User + Agent)
            MAX_MESSAGES = 10 
            
            # Access the active context directly
            ctx = agent.chat_ctx
            
            # Check if the list exceeds our limit (System Prompt + MAX_MESSAGES)
            if len(ctx.messages) > MAX_MESSAGES + 1:
                
                # 1. Save the System Prompt (always at index 0)
                system_prompt = ctx.messages[0]
                
                # 2. Slice out only the most recent N messages
                recent_messages = ctx.messages[-MAX_MESSAGES:]
                
                # 3. Clear and rebuild the list in-place to avoid race conditions
                ctx.messages.clear()
                ctx.messages.append(system_prompt)
                ctx.messages.extend(recent_messages)
                
                logger.info(f"Sliding window applied: Context trimmed to {len(ctx.messages)} messages.")
                
    # Start the session
    await session.start(room=ctx.room, agent=agent)
    logger.info("Agent started and connected to the room...")

    # Wait 1.5 seconds to ensure the WebRTC audio connection is fully stabilized
    await asyncio.sleep(0.2)
    
    logger.info("Triggering proactive greeting...")
    
    # In LiveKit v1.5+, this is the correct way to make the agent speak first
    await session.say(
        text=greeting_text,
        add_to_chat_ctx=True
    )

if __name__ == "__main__":
    cli.run_app(server)