"""Retrieval Multi-Agent - 진입점"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

# 에이전트 응답 로그는 항상 출력 (verbose 무관)
_agent_logger = logging.getLogger("retriever.agents")
_agent_logger.setLevel(logging.INFO)


def main():
    parser = argparse.ArgumentParser(
        description="Retrieval Multi-Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py "재난 관리 체계에서 중앙정부의 역할은 무엇인가?"
  python main.py "2023년 GDP 성장률은?" --verbose
""",
    )
    parser.add_argument("query", help="사용자 질의")
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="상세 로그 출력",
    )
    args = parser.parse_args()

    if not args.verbose:
        # 루트 로거는 WARNING으로 낮추되, 에이전트 응답 로거는 INFO 유지
        logging.getLogger().setLevel(logging.WARNING)
        _agent_logger.setLevel(logging.INFO)

    from agents import orchestrator

    print(f"\n[질의] {args.query}\n")
    result = orchestrator.run(args.query)

    if result["status"] == "success":
        print("=" * 60)
        print(result["answer"])
        print("=" * 60)
        if result.get("sources"):
            print("\n[출처]")
            for src in result["sources"]:
                print(f"  - {src}")
    else:
        print(f"[실패] {result['detail']}")
        if result.get("partial_result"):
            print("\n[부분 결과]")
            print(result["partial_result"])

    # JSON 전체 결과 덤프 (verbose 모드)
    if args.verbose:
        print("\n[전체 결과 JSON]")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
