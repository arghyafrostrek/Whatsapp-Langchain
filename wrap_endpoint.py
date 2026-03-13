import re

with open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
in_chat = False
chat_done = False

for i, line in enumerate(lines):
    if not in_chat and line.startswith('@app.post("/chat")'):
        new_lines.append(line)
        in_chat = True
        continue
    
    if in_chat and not chat_done and line.startswith('async def chat_endpoint(request: Request) -> Any:'):
        new_lines.append(line)
        new_lines.append('    session_id = None\n')
        new_lines.append('    try:\n')
        continue

    # We are inside the function
    if in_chat and not chat_done:
        # Check if we've hit the next public endpoint, which indicates we're done
        if line.startswith('# ---- Public API v1 ----'):
            # Insert the except block before closing out
            new_lines.append('    except Exception as e:\n')
            new_lines.append('        logger.error(\n')
            new_lines.append('            f"chat_endpoint_unhandled_error "\n')
            new_lines.append('            f"session={session_id} error={e}",\n')
            new_lines.append('            exc_info=True\n')
            new_lines.append('        )\n')
            new_lines.append('        return JSONResponse(\n')
            new_lines.append('            sanitize_response(\n')
            new_lines.append('                "normal_chat",\n')
            new_lines.append('                "Sorry, I\'m having trouble "\n')
            new_lines.append('                "right now. Please try again."\n')
            new_lines.append('            ),\n')
            new_lines.append('            status_code=200\n')
            new_lines.append('        )\n\n')
            
            new_lines.append(line)
            in_chat = False
            chat_done = True
            continue
            
        # If it's empty, just append
        if not line.strip():
            new_lines.append(line)
        else:
            # Indent
            new_lines.append('    ' + line)
        continue

    # Not in chat function, or already past it
    new_lines.append(line)

with open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\app.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print("Wrapped chat_endpoint in try-except block safely!")
