@session.on("agent_state_changed")
    def apply_sliding_window(ev):
        if "listening" in str(ev.new_state).lower():
            MAX_MESSAGES = 6 
            
            # Safely handle LiveKit v1.5's _ReadOnlyChatContext
            raw_messages = agent.chat_ctx.messages
            current_messages = raw_messages() if callable(raw_messages) else raw_messages
            
            if len(current_messages) > MAX_MESSAGES + 1:
                # Keep System Prompt (0) and slice the latest N messages
                sliced_messages = [current_messages[0]] + current_messages[-MAX_MESSAGES:]
                
                # --- THE FIX ---
                # Initialize an empty context, then extend it with your sliced list
                new_ctx = llm.ChatContext()
                new_ctx.messages.extend(sliced_messages)
                
                # Define a quick async helper inside the function to run the update
                async def update_memory():
                    await agent.update_chat_ctx(new_ctx)
                    logger.info(f"Sliding window applied: Context truncated to {len(sliced_messages)} messages.")
                
                # Fire it off using create_task so it doesn't block the event loop
                asyncio.create_task(update_memory())