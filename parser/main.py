"""Document Parser Workflow - 진입점"""
import sys
import json
import argparse
from pathlib import Path

import workflow


def main():
    parser = argparse.ArgumentParser(
        description="Document Parser Workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 실행 (중단 후 재실행 시 완료된 step 자동 건너뜀)
  python main.py run ./sample.hwpx
  python main.py run ./report.pdf

  # 현재 상태 조회
  python main.py status ./report.pdf
""",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── Document Parser Workflow ───────────────────────────
    run_parser = subparsers.add_parser("run", help="워크플로우 실행 (중단 시 재개 가능)")
    run_parser.add_argument("file", help="처리할 파일 경로")

    inspect_parser = subparsers.add_parser("inspect", help="섹션 목록 또는 특정 섹션 내용 조회")
    inspect_parser.add_argument("file", help="처리할 파일 경로")
    inspect_parser.add_argument("--section", help="조회할 섹션 번호(seq) 또는 제목 일부", default=None)

    reset_parser = subparsers.add_parser("reset", help="특정 step 초기화 (재실행 허용)")
    reset_parser.add_argument("file", help="처리할 파일 경로")
    reset_parser.add_argument("step", help="초기화할 step 이름 (예: index_parser)")

    status_parser = subparsers.add_parser("status", help="파일의 워크플로우 진행 상태 조회")
    status_parser.add_argument("file", help="처리할 파일 경로")
    status_parser.add_argument("--step", help="특정 step의 result만 출력 (예: index_parser)", default=None)

    args = parser.parse_args()

    # ── Document Parser Workflow 명령 처리 ───────────────────
    file_path = Path(args.file)

    if args.command == "run":
        if not file_path.exists():
            print(f"[ERROR] 파일이 존재하지 않습니다: {file_path}", file=sys.stderr)
            sys.exit(1)
        try:
            context = workflow.run(file_path)
            print(json.dumps(context, ensure_ascii=False, indent=2, default=str))
        except FileNotFoundError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"[ERROR] 워크플로우 실패: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "inspect":
        import db as _db
        _db.init_schema()
        job_id = _db.get_job_id(str(file_path))
        if job_id is None:
            print(f"[INFO] 데이터 없음: {file_path}")
            sys.exit(0)

        with _db.connect() as conn:
            with conn.cursor() as cur:
                if args.section is None:
                    # 섹션 목록 출력
                    cur.execute(
                        "SELECT seq, level, title, "
                        "(SELECT COUNT(*) FROM parser_blocks b WHERE b.section_id = s.id) "
                        "FROM parser_sections s WHERE document_id = %s ORDER BY seq",
                        (job_id,),
                    )
                    rows = cur.fetchall()
                    print(f"{'seq':>4}  {'lv'}  {'blocks':>6}  title")
                    print("-" * 60)
                    for seq, level, title, cnt in rows:
                        indent = "  " * (level - 1) if level else ""
                        print(f"{seq:>4}  {level}   {cnt:>5}  {indent}{title}")
                else:
                    # 특정 섹션 내용 출력
                    try:
                        seq = int(args.section)
                        cur.execute(
                            "SELECT id, title, section_path FROM parser_sections "
                            "WHERE document_id = %s AND seq = %s",
                            (job_id, seq),
                        )
                    except ValueError:
                        cur.execute(
                            "SELECT id, title, section_path FROM parser_sections "
                            "WHERE document_id = %s AND title ILIKE %s ORDER BY seq LIMIT 1",
                            (job_id, f"%{args.section}%"),
                        )
                    row = cur.fetchone()
                    if not row:
                        print(f"[INFO] 섹션 없음: {args.section}")
                        sys.exit(0)
                    sec_id, title, path = row
                    print(f"=== {path} ===\n")

                    # 블록
                    cur.execute(
                        "SELECT seq, block_type, content FROM parser_blocks "
                        "WHERE section_id = %s ORDER BY seq",
                        (sec_id,),
                    )
                    for bseq, btype, content in cur.fetchall():
                        print(f"--- [{btype}] block {bseq} ---")
                        print(content or "")
                        print()

                    # 명제
                    cur.execute(
                        "SELECT seq, proposition, keywords FROM parser_propositions "
                        "WHERE section_id = %s ORDER BY seq",
                        (sec_id,),
                    )
                    props = cur.fetchall()
                    if props:
                        print("--- [propositions] ---")
                        for pseq, prop, kw in props:
                            kw_list = json.loads(kw) if isinstance(kw, str) else (kw or [])
                            kw_str = ", ".join(kw_list)
                            print(f"  {pseq}. {prop}")
                            if kw_str:
                                print(f"     키워드: {kw_str}")
                        print()

    elif args.command == "reset":
        workflow.reset(file_path, args.step)
        print(f"[INFO] reset 완료: {args.step} → 다음 run 시 재실행됩니다")

    elif args.command == "status":
        result = workflow.status(file_path)
        if result is None:
            print(f"[INFO] job 없음: {file_path}")
        elif args.step:
            step = next((s for s in result["steps"] if s["step_name"] == args.step), None)
            if step is None:
                print(f"[INFO] step 없음: {args.step}")
            else:
                print(json.dumps(step.get("result"), ensure_ascii=False, indent=2, default=str))
        else:
            # 기본 출력: result 제외한 요약
            summary = {
                "job": result["job"],
                "steps": [
                    {k: v for k, v in s.items() if k != "result"}
                    for s in result["steps"]
                ],
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
