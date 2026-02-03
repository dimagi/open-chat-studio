import sys

from agent import NLFilterAgent


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_agent.py 'your query here'")
        print("   or: python test_agent.py 'your query here' --feedback correct --trace-id abc-123")
        sys.exit(1)

    query = sys.argv[1]

    if "--feedback" in sys.argv:
        feedback_idx = sys.argv.index("--feedback")
        trace_id_idx = sys.argv.index("--trace-id")

        feedback = sys.argv[feedback_idx + 1]
        trace_id = sys.argv[trace_id_idx + 1]

        agent = NLFilterAgent()
        is_correct = feedback == "correct"
        agent.record_feedback(trace_id, is_correct)
        agent.flush()
        print(f"✓ Feedback recorded for trace {trace_id}")
        return

    agent = NLFilterAgent()
    result = agent.translate(query)

    print(f"\Query: {query}\n")
    print("Translation:")
    print(f"   {result['filter_query_string']}")
    print("Explanation:")
    print(f"   {result['explanation']}")
    print(f"\nConfidence: {result['confidence']:.0%}")

    if result.get("trace_id"):
        print(f"\nTrace ID: {result['trace_id']}")
        print("\nTo record feedback, run:")
        print(f"   python test_agent.py '{query}' --feedback correct --trace-id {result['trace_id']}")

    agent.flush()


if __name__ == "__main__":
    main()
