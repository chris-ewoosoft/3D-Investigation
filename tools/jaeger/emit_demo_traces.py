"""Demo script to emit realistic AI Assistant spans to Jaeger via OpenTelemetry."""
import sys
import time
from pathlib import Path

# Add AIAssistant to sys.path
package_root = Path(__file__).resolve().parents[2] / "AIAssistant"
sys.path.insert(0, str(package_root / "src"))

from ai_assistant.settings import apply_startup_environment_defaults, load_local_dotenv
load_local_dotenv(package_root)
apply_startup_environment_defaults()

from ai_assistant.observability.tracing import _get_tracer, span
from opentelemetry import trace

def run_demo_agent_flow():
    print("[1/4] Checking OpenTelemetry Tracer configuration...")
    tracer = _get_tracer()
    if not tracer:
        print("ERROR: Tracer could not be initialized. Check AGENT_OBSERVABILITY in .env")
        return

    print(" -> Tracer initialized:", tracer)
    print("[2/4] Emitting nested Agent spans to Jaeger...")

    # Root span: User request / Agent supervisor
    with span("agent.supervisor", session_id="session-demo-01", task="Analyze 3D Mesh & Point Cloud"):
        print("   -> [Root Span] agent.supervisor started")
        time.sleep(0.06)

        # Child 1: RAG retrieval
        with span("agent.rag.retrieve_context", query="point cloud segmentation threshold", top_k=5):
            print("      -> [Child Span] agent.rag.retrieve_context (vector search)")
            time.sleep(0.08)

        # Child 2: Tool execution batch
        with span("agent.tool_batch", branches=2):
            print("      -> [Child Span] agent.tool_batch (parallel tool calls)")
            with span("agent.tool.inspect_ply_metadata", file_path="recon_mesh_v1.ply", vertices=84200):
                time.sleep(0.05)
            with span("agent.tool.calculate_bounding_box", bounds_x=[-10.5, 10.5], bounds_y=[-5.2, 5.2]):
                time.sleep(0.04)

        # Child 3: Response generation
        with span("agent.llm.generate_response", model="llama-3-8b", tokens_generated=185, prompt_tokens=420):
            print("      -> [Child Span] agent.llm.generate_response")
            time.sleep(0.12)

    print("[3/4] Flushing spans through OpenTelemetry BatchSpanProcessor...")
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()
    time.sleep(1)

    print("[4/4] Done! Traces sent to Jaeger OTLP endpoint.")
    print("------------------------------------------------------------")
    print(">> Open your browser at: http://localhost:16686")
    print(">> Service: 'ai-assistant'")
    print(">> Click 'Find Traces' to view the Gantt timeline!")
    print("------------------------------------------------------------")

if __name__ == "__main__":
    run_demo_agent_flow()

