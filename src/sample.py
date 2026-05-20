import asyncio
import logging
import json
import re
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
    room_io,
    llm,
    function_tool, 
    RunContext
)
# IMPORT THE NEW PLUGINS HERE
from livekit.plugins import ai_coustics, noise_cancellation, silero, groq, sarvam
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")
load_dotenv(".env.local")

import typesense
ts_client = typesense.Client({
  'nodes': [{
    'host': 'qjwn6bo8plhax0rdp-1.a2.typesense.net',
    'port': '443',
    'protocol': 'https'
  }],
  'api_key': '083TWWQruebrJtdpvaPMB1kv7DW60Pui', # Better to move this to .env.local!
  'connection_timeout_seconds': 2
})
class Assistant(Agent):
    def __init__(self, room: rtc.Room) -> None:
        self.room = room
        # ==========================================
        # 1. SYSTEM PROMPT (The Static Rules)
        # ==========================================
        super().__init__(
            instructions="""You are a professional real estate agent from Property First. Keep responses conversational, concise, and absolutely free of markdown formatting.

You operate strictly based on the CURRENT_STAGE provided in your real-time data state.

### STAGE 1 Rules (Establish Interest)
- Goal: Find out if the user is interested in property investment.
- Keep it casual. E.g., "Hi, this is Property First. Are you looking to invest in real estate?"
- Once they answer, immediately call the `record_interest` tool. Do not ask further questions.

### STAGE 2 Rules (Gather Preferences)
- Goal: Collect exactly 4 preferences: Location, Budget, Builder, and Project.
- Even if they give you Location and Budget, you MUST ask if they have a preferred Builder or Project in mind.
- If they say no to Builder or Project, treat it as "any".
- Convert conversational budgets to numbers (e.g., "seven and a half" -> "7.5 cr").
- ONLY call `search_properties` AFTER you have asked about all 4 fields.

### STAGE 3 Rules (Pitching Properties)
- Look at the "properties" list and "current_index" in your state.
- Pitch ONLY the property at the current_index (Project Name, Location, Price, 1 USP). 
- Immediately ask: "Would you like me to send you the brochure for this property?"
- CRITICAL: When the user answers this brochure question, you MUST call `record_brochure_decision`. NEVER call `record_interest` in Stage 3.
- If they interrupt to change a preference, call `search_properties` again.

### CRITICAL RULES FOR TOOLS
- NEVER call the same tool twice in a single turn. 
- Once a tool returns a success message, you MUST immediately stop calling tools and speak to the user.
"""
        )
        
        # ==========================================
        # 2. STATE MACHINE (The Live Data)
        # ==========================================
        self.app_state = {
            "stage": 1,
            "preferences": {
                "location": None,
                "budget": None,
                "builder": "any",
                "project": "any"
            },
            "properties": [],
            "current_index": 0
        }

    # ==========================================
    # 3. MEMORY INTERCEPTOR (LiveKit 1.5+ Synchronous Format)
    # ==========================================
    # ==========================================
    # 3. MEMORY INTERCEPTOR (LiveKit 1.5+ Fix)
    # ==========================================
    # ==========================================
    # 3. MEMORY INTERCEPTOR (Optimized Context)
    # ==========================================
    def llm_node(self, chat_ctx: llm.ChatContext, tools: list, model_settings: llm.ModelSettings):
        new_ctx = llm.ChatContext()
        
        # 1. Add the Global System Instructions (The rules)
        # We find the first system message which contains your main prompt
        for msg in chat_ctx.messages():
            if msg.role == "system":
                new_ctx.add_message(role="system", content=msg.content)
                break 
                
        # 2. Inject the Real-Time Data State
        # This tells the LLM exactly what happened in the "backend"
        state_string = json.dumps(self.app_state)
        new_ctx.add_message(
            role="system", 
            content=f"CURRENT REAL-TIME DATA STATE: {state_string}. Strictly follow the rules for the 'stage' in this data."
        )
        
        # 3. Add ONLY the most recent Assistant question (if any)
        # This helps the LLM remember what it JUST asked the user.
        assistant_msgs = [msg for msg in chat_ctx.messages() if msg.role == "assistant"]
        if assistant_msgs:
            new_ctx.add_message(role="assistant", content=assistant_msgs[-1].content)

        # 4. Add ONLY the most recent User reply
        # This is the current input the LLM needs to process.
        user_msgs = [msg for msg in chat_ctx.messages() if msg.role == "user"]
        if user_msgs:
            new_ctx.add_message(role="user", content=user_msgs[-1].content)
            
        # Now the LLM sees: Rules -> Current State -> Last Question -> Current Answer.
        return super().llm_node(new_ctx, tools, model_settings)

    # ==========================================
    # 4. FUNCTION TOOLS (The Actions)
    # ==========================================
    @function_tool
    async def record_interest(self, context: RunContext, is_interested: bool):
        """
        ONLY USE THIS IN STAGE 1.
        Records if the user is interested in investing.
        """
        # 1. The Hard Guardrail: Block repeated calls
        if self.app_state.get("interest_recorded"):
            return "SYSTEM ERROR: You already called this tool! You are stuck in a loop. STOP calling tools immediately and ask the user for their Location, Budget, Builder, and Project."
        
        # 2. Record the state so it can't be called again
        self.app_state["interest_recorded"] = True
        self.app_state["is_interested"] = is_interested

        # 3. The First-Time Success Message (Bossy and direct)
        if is_interested:
            return "Success. Interest recorded. STOP calling tools right now. Speak to the user and ask for their preferred Location, Budget, Builder, and Project."
        else:
            return "User is not interested. STOP calling tools and politely end the conversation."

    



    @function_tool
    async def search_properties(self, context: RunContext, location: str, budget: str, builder: str = "any", project: str = "any"):
        """
        Searches the database for properties. Call this when you have gathered preferences, 
        OR when the user interrupts during Stage 3 to change a preference.
        """
        self.app_state["preferences"] = {
            "location": location, "budget": budget, "builder": builder, "project": project
        }
        
        print(f"Executing Typesense Search for: Loc:{location}, Budg:{budget}, Build:{builder}, Proj:{project}")
        
        # --- 1. INITIAL SEARCH (Exact Preferences) ---
        search_terms = []
        if location and location.lower() != "any": search_terms.append(location)
        if builder and builder.lower() != "any": search_terms.append(builder)
        if project and project.lower() != "any": search_terms.append(project)
            
        query_string = " ".join(search_terms) if search_terms else "*"

        search_parameters = {
            'q': query_string,
            'query_by': 'location,builder,project_name,region', 
            'per_page': 5 
        }

        # Handle Budget Math
        max_budget = None
        if budget and budget.lower() != "any":
            match = re.search(r"(\d+(\.\d+)?)", budget)
            if match:
                budget_num = float(match.group(1))
                max_budget = budget_num * 1.1 
                search_parameters['filter_by'] = f'starting_price_cr:<={max_budget}'
                search_parameters['sort_by'] = 'starting_price_cr:desc' 

        fetched_properties = []
        fallback_triggered = False

        try:
            search_results = ts_client.collections['real_estate_projects'].documents.search(search_parameters)
            hits = search_results.get('hits', [])
            
            for hit in hits:
                doc = hit['document']
                fetched_properties.append({
                    "project": doc.get('project_name', 'Unknown Project'),
                    "builder": doc.get('builder', 'Unknown Builder'),
                    "location": doc.get('location', 'Unknown Location'),
                    "price": f"{doc.get('starting_price_cr')} Cr", 
                    "usp": doc.get('usp', 'Premium amenities and great connectivity')
                })
                
        except Exception as e:
            print(f"Typesense Error on initial search: {e}")

        # --- 2. FALLBACK SEARCH (Budget Only) ---
        # If no properties were found, AND the user provided a budget, search again ignoring location/builder
        if not fetched_properties and max_budget is not None:
            print("No exact matches found. Triggering Fallback Search (Budget Only)...")
            fallback_parameters = {
                'q': '*', # Match anything
                'query_by': 'project_name', # Required by Typesense but ignored due to '*'
                'filter_by': f'starting_price_cr:<={max_budget}',
                'sort_by': 'starting_price_cr:desc',
                'per_page': 5
            }
            
            try:
                fallback_results = ts_client.collections['real_estate_projects'].documents.search(fallback_parameters)
                fallback_hits = fallback_results.get('hits', [])
                
                for hit in fallback_hits:
                    doc = hit['document']
                    fetched_properties.append({
                        "project": doc.get('project_name', 'Unknown Project'),
                        "builder": doc.get('builder', 'Unknown Builder'),
                        "location": doc.get('location', 'Unknown Location'),
                        "price": f"{doc.get('starting_price_cr')} Cr", 
                        "usp": doc.get('usp', 'Premium amenities and great connectivity')
                    })
                
                if fetched_properties:
                    fallback_triggered = True # Mark that we had to use the fallback
                    
            except Exception as e:
                print(f"Typesense Error on fallback search: {e}")

        # --- 3. UPDATE STATE AND RETURN INSTRUCTIONS TO LLM ---
        self.app_state["properties"] = fetched_properties
        self.app_state["current_index"] = 0
        self.app_state["stage"] = 3
        
        if fetched_properties:
            if fallback_triggered:
                # Tell the LLM to pivot its pitch!
                return f"Found {len(fetched_properties)} properties using a BUDGET-ONLY search because their exact preferences yielded 0 results. Transitioned to Stage 3. Inform the user that you couldn't find exact matches for their preferences, but suggest these budget-friendly alternatives instead. Pitch the property at index 0 now."
            else:
                # Normal pitch
                return f"Found {len(fetched_properties)} exact matches. Transitioned to Stage 3. Pitch the first property at index 0 now."
        else:
            # Absolute worst-case scenario (No properties even exist under that budget anywhere)
            return "No properties found anywhere under their budget. Inform the user politely and ask if they are open to increasing their budget."
    @function_tool
    async def record_brochure_decision(self, context: RunContext, wants_brochure: bool):
        """
        ONLY USE THIS IN STAGE 3. 
        Records whether the user wants the brochure for the currently pitched property. 
        """
        if wants_brochure:
            # --- NEW DISCONNECT LOGIC ---
            async def delayed_disconnect():
                await asyncio.sleep(5) # Wait for the TTS goodbye to finish
                print("Disconnecting room...")
                await self.room.disconnect()

            # Fire the disconnect task in the background
            asyncio.create_task(delayed_disconnect())
            
            # Tell the LLM to wrap it up
            return "User wants the brochure. The call will drop in 5 seconds. Tell them you will send it shortly, say a brief goodbye, and STOP calling tools."
            
        else:
            self.app_state["current_index"] += 1
            if self.app_state["current_index"] >= len(self.app_state["properties"]):
                return "There are no more properties. Tell the user you've exhausted the list and ask if they'd like to adjust their budget or location. DO NOT call this tool again."
            else:
                return "User declined this brochure. Look at the properties list and pitch the next property. DO NOT call this tool again."

    @function_tool
    async def end_call(self, context: RunContext):
        """
        ONLY USE THIS IN STAGE 3.
        Call this tool ONLY when the user wants to end the conversation, 
        or after you have promised to send them the brochure and the workflow is completely finished.
        """
        async def delayed_disconnect():
            # Wait for 5 seconds to let the TTS finish speaking the final goodbye
            await asyncio.sleep(5) 
            print("Disconnecting room...")
            await self.room.disconnect()

        # Fire the disconnect task in the background
        asyncio.create_task(delayed_disconnect())
        
        # Tell the LLM to wrap it up instantly
        return "The call will drop in 5 seconds. Say a very brief, professional final goodbye to the user right now."
# ==========================================
# 5. SERVER AND SESSION SETUP
# ==========================================
server = AgentServer()

def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

server.setup_fnc = prewarm

@server.rtc_session(agent_name="real-estate-agent")
async def my_agent(ctx: JobContext):
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        
        # --- NEW GROQ LLM ---
        # Llama 3.3 70B is extremely fast and smart for function calling
        llm=groq.LLM(model="llama-3.3-70b-versatile",parallel_tool_calls=False), 
        
        # --- NEW SARVAM TTS ---
        # Using English with Indian context (en-IN) and the default male speaker 'shubh'
        tts=sarvam.TTS(
            target_language_code="en-IN", 
            speaker="anushka" 
        ),
        
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    agent = Assistant(room=ctx.room)

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else ai_coustics.audio_enhancement(
                        model=ai_coustics.EnhancerModel.QUAIL_VF_L
                    )
                ),
            ),
        ),
    )

    await ctx.connect()
    
    # Kick off the conversation
    await session.say("Hi, this is Property First. Are you looking to invest in real estate?", allow_interruptions=True)

if __name__ == "__main__":
    cli.run_app(server)