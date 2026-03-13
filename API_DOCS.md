# Frosty API Documentation

## `/chat` Endpoint Responses

No matter the inner state, the `/chat` endpoint is guaranteed to return a consistent JSON payload structure:

```json
{
  "intent": "string",
  "reply": "string",
  "continuous": false
}
```

### Intent Values Clients Should Handle

- `"normal_chat"`
  - **Action**: Display the `reply` text to the user.
- `"awaiting_confirmation"`
  - **Action**: Display the `reply` text to the user and visually present a Yes/No UI allowing the user to confirm an impending action.
- `"email_sent"`
  - **Action**: Display the `reply` text to the user alongside a success indicator or toast.
- `"cancelled"`
  - **Action**: Display the `reply` text to the user indicating the operation was aborted.
- `"ask_email"`
  - **Action**: Indicates the user should be prompted for their email address.
  
### Continuous Mode
- `continuous: true` implies that the bot expects a follow-up response from the user (such as a yes/no confirmation) in the immediate next request. Clients can use this to keep voice microphones active or prompt the user directly.
================================================
##ECHO LOOP PREVENTION:
The backend automatically detects and drops
transcriptions that match the bot's own 
previous responses.

Recommended frontend implementation:
- Disable mic during audio playback
- Add 500ms delay after playback ends
  before re-enabling mic recording
- This prevents echo detection false positives
  on legitimate messages
======================================================

  ##SILENT RESPONSE:
When intent = "echo_suppressed":
  - reply will be ""
  - silent = true
  - Frontend should NOT display anything
  - Frontend should NOT play any audio
  - This means echo was detected and dropped

Recommended frontend handling:
  if (data.intent === "echo_suppressed") {
      return; // do nothing, ignore silently
  }