"""
AI Ticketing Agent — Gradio Chat UI.

Launches a Gradio chat interface for investigating human-created incidents.
The user provides an incident ID and the agent reads the incident, troubleshoots
using OpenShift and Prometheus MCP servers, and updates the incident with findings.
"""

from __future__ import annotations

import asyncio
import logging
import os

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr

from agent import run_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

EXAMPLE_PROMPTS = [
    "Investigate incident INC0000001. Read the incident, troubleshoot the reported issue on OpenShift, and update the ticket with your findings.",
    "Analyze incident INC0000002. Check pod health and Prometheus metrics for the application mentioned in the ticket, then update it with a diagnosis.",
    "Look at incident INC0000003 and determine if the issue is still happening. Update the ticket with current cluster state.",
    "List all open incidents and pick the highest priority one to investigate.",
]

HEADER_HTML = """
<div style="text-align:center; padding: 12px 0 4px;">
  <h1 style="font-size:1.8rem; margin:0;">🎫 AI Ticketing Agent</h1>
  <p style="color:#666; margin:4px 0 0;">
    Powered by <strong>Nemotron 3 Nano 30B</strong> · OpenShift MCP · Prometheus / Thanos · Ticketing System
  </p>
</div>
"""


async def chat(message: str, history: list[list[str]]) -> gr.ChatMessage:
    """
    Gradio streaming chat handler.

    Receives the user message and conversation history, streams the agent
    response back chunk by chunk.
    """
    if not message.strip():
        yield history + [{"role": "assistant", "content": "Please enter an incident number or investigation request."}]
        return

    logger.info("User: %s", message[:120])

    history = history + [{"role": "user", "content": message}]
    yield history

    response_so_far = ""
    history = history + [{"role": "assistant", "content": ""}]

    async for chunk in run_agent(message):
        response_so_far += chunk
        history[-1] = {"role": "assistant", "content": response_so_far}
        yield history


def build_ui() -> gr.Blocks:
    with gr.Blocks(
        title="AI Ticketing Agent",
        theme=gr.themes.Soft(
            primary_hue="blue",
            secondary_hue="orange",
            neutral_hue="slate",
        ),
        css="""
        .chatbot { background: #ffffff !important; border-radius: 8px; }
        .chatbot .message { font-size: 0.95rem; }
        .chatbot pre { background: #1e1e2e; color: #cdd6f4; padding: 12px; border-radius: 6px; }
        footer { display: none !important; }
        """,
    ) as demo:
        gr.HTML(HEADER_HTML)

        chatbot = gr.Chatbot(
            label="Incident Investigator",
            height=560,
            show_copy_button=True,
            render_markdown=True,
            type="messages",
            avatar_images=(
                None,
                "https://www.redhat.com/cms/managed-files/Asset-Red_Hat-Logo_page-Logo-RGB.svg",
            ),
        )

        with gr.Row():
            txt = gr.Textbox(
                placeholder="Enter an incident ID (e.g. INC0000001) or ask the agent to investigate a ticket…",
                show_label=False,
                scale=9,
                container=False,
            )
            send_btn = gr.Button("Investigate", variant="primary", scale=1, min_width=80)

        gr.Examples(
            examples=EXAMPLE_PROMPTS,
            inputs=txt,
            label="Quick-start prompts",
        )

        with gr.Accordion("ℹ️ About this agent", open=False):
            gr.Markdown(
                """
                This AI agent investigates human-created incidents by correlating ticketing
                data with live cluster state.

                **How it works:**
                1. You provide an incident number (e.g. `INC0000001`)
                2. The agent reads the incident from the **Ticketing System**
                3. It queries the **OpenShift MCP server** for pod status, events, and logs
                4. It queries **Prometheus / Thanos** for HTTP error rates and latency metrics
                5. **Nemotron 3 Nano 30B** correlates the findings with the incident description
                6. The agent **updates the incident** with a full investigation report and possible resolution
                """
            )

        submit_event = txt.submit(
            fn=chat,
            inputs=[txt, chatbot],
            outputs=[chatbot],
        )
        submit_event.then(lambda: "", outputs=[txt])

        send_event = send_btn.click(
            fn=chat,
            inputs=[txt, chatbot],
            outputs=[chatbot],
        )
        send_event.then(lambda: "", outputs=[txt])

    return demo


if __name__ == "__main__":
    port = int(os.getenv("GRADIO_PORT", "7860"))
    demo = build_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        show_api=False,
    )
