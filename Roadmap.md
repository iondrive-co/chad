v0.11 Epochalypse

- Event loop to extract progress updates from agent stream and notify API listeners
- Option to resume task once usage limit has reset rather than switch to another provider
- Receive tasks from slack and output progress from event loop
- Escalate verification requirements over time
- Typescript ui for later use in expo app and static site

v0.12 Agent Ick

- Queue of bugs/features. Get agent to add to this list as it finds issues while coding
- Integrate with `cloudflared` to create tunnels on demand (`chad --remote`), exposing the local API server through a 
Cloudflare tunnel. A lightweight Cloudflare Worker backed by Workers KV will provide token-based pairing: Chad registers 
a short-lived pairing code (e.g., "TIGER-42") mapped to the tunnel URL, and the browser UI resolves the code to connect. 
This enables accessing Chad from any device by entering a code on the hosted UI page, with no relay server—Cloudflare 
proxies traffic directly to the user's machine through the tunnel.

v0.13 Intentionality Reduction

Planning agent
Packaging for different platforms

v0.14 Slip Slop Slap

Resume session at startup
Session branching
