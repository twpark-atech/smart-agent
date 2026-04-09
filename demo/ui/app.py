"""Smart Agent 데모 UI — Streamlit
실행: streamlit run ui/app.py
"""
import os
import time
import requests
import streamlit as st

INDEXER_URL = os.getenv("INDEXER_URL", "http://localhost:8001")
SEARCHER_URL = os.getenv("SEARCHER_URL", "http://localhost:8002")

st.set_page_config(
    page_title="Smart Agent Demo",
    page_icon="🤖",
    layout="wide",
)

# ── 사이드바 ─────────────────────────────────────────────────

with st.sidebar:
    st.title("🤖 Smart Agent")
    st.caption("RAG 기반 문서 지식 검색 데모")
    st.divider()

    # 서버 상태
    st.subheader("서버 상태")
    col1, col2 = st.columns(2)
    with col1:
        try:
            r = requests.get(f"{INDEXER_URL}/health", timeout=3)
            stats = r.json().get("index_stats", {})
            st.success("Indexer ✓")
        except Exception:
            stats = {}
            st.error("Indexer ✗")
    with col2:
        try:
            requests.get(f"{SEARCHER_URL}/health", timeout=3)
            st.success("Searcher ✓")
        except Exception:
            st.error("Searcher ✗")

    if stats:
        doc_count_key = [k for k in stats if "documents" in k]
        chunk_count_key = [k for k in stats if "chunks" in k]
        doc_cnt = stats[doc_count_key[0]] if doc_count_key else 0
        chunk_cnt = stats[chunk_count_key[0]] if chunk_count_key else 0
        st.metric("인덱싱된 문서", doc_cnt)
        st.metric("총 청크 수", chunk_cnt)

    st.divider()
    top_k = st.slider("Top-K 검색 수", min_value=3, max_value=20, value=5)
    top_domain = st.slider("도메인 후보 수", min_value=1, max_value=5, value=3,
                           help="검색 시 고려할 도메인 후보 개수. 많을수록 넓은 범위를 검색합니다.")

# ── 공통 헬퍼 ────────────────────────────────────────────────

def _render_search_detail(meta: dict):
    track = meta.get("track", "")
    st.caption(f"Track **{track}** 검색")

    # 쿼리 변환
    original = meta.get("original_query", "")
    rewritten = meta.get("rewritten_query", "")
    if rewritten and rewritten != original:
        st.write("**쿼리 변환:**")
        st.caption(f"원본: {original}")
        st.info(f"변환: {rewritten}")
    elif rewritten:
        st.write(f"**검색 쿼리:** {rewritten}")

    # 도메인 후보 (Track A)
    candidates = meta.get("domain_candidates", [])
    if candidates:
        labels = [f"`{i + 1}. {c.get('domain_category', '')}`" for i, c in enumerate(candidates)]
        st.write("**도메인 후보:** " + "  ".join(labels))
    if meta.get("domain_fallback"):
        st.warning("도메인 필터 미적용 재검색 (도메인 매칭 문서 없어 전체 범위로 재시도)")

    # 서브 쿼리 (Track B)
    if meta.get("sub_queries"):
        st.write("**서브 쿼리 분해:**")
        for sq in meta["sub_queries"]:
            if isinstance(sq, dict):
                dc = sq.get("domain_category", "")
                domain_str = f" `{dc}`" if dc else ""
                st.markdown(f"- {sq['query']}{domain_str}")
            else:
                st.markdown(f"- {sq}")

    if meta.get("validation"):
        v = meta["validation"]
        score = v.get("score", 0)
        color = "🟢" if score >= 0.7 else "🟡" if score >= 0.4 else "🔴"
        st.write(f"**검증 점수:** {color} {score:.2f} — {v.get('message', '')}")
    if meta.get("docs"):
        st.write("**참조 문서:**")
        for d in meta["docs"]:
            st.markdown(f"- {d.get('title', d.get('document_id', ''))}")
    if meta.get("chunks"):
        st.write("**참조 청크:**")
        for c in meta["chunks"]:
            with st.container():
                st.markdown(f"**{c.get('section_path', '')}**")
                st.caption(c.get("proposition", ""))
                st.text(c.get("content_preview", "")[:300])


# ── 탭 ──────────────────────────────────────────────────────

tab_search, tab_docs = st.tabs(["💬 RAG 검색", "📂 문서 관리"])


# ════ Tab 1: RAG 검색 ═══════════════════════════════════════

with tab_search:
    st.header("RAG 검색")

    # 채팅 히스토리 초기화
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # 이전 메시지 렌더링
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("meta"):
                with st.expander("검색 상세 정보", expanded=False):
                    _render_search_detail(msg["meta"])

    # 입력
    query = st.chat_input("문서에 대해 질문하세요...")
    if query:
        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"):
            st.markdown(query)

        with st.chat_message("assistant"):
            with st.spinner("검색 중..."):
                try:
                    resp = requests.post(
                        f"{SEARCHER_URL}/search",
                        json={"query": query, "top_k": top_k, "top_domain": top_domain},
                        timeout=120,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    answer = data.get("answer", "답변을 생성하지 못했습니다.")
                    meta = {
                        "track": data.get("track"),
                        "original_query": data.get("original_query"),
                        "rewritten_query": data.get("rewritten_query"),
                        "domain_candidates": data.get("domain_candidates", []),
                        "domain_fallback": data.get("domain_fallback", False),
                        "sub_queries": data.get("sub_queries"),
                        "validation": data.get("validation"),
                        "docs": data.get("docs", []),
                        "chunks": data.get("chunks", []),
                    }
                except requests.exceptions.ConnectionError:
                    answer = "검색 서버에 연결할 수 없습니다. Searcher가 실행 중인지 확인하세요."
                    meta = None
                except Exception as e:
                    answer = f"오류가 발생했습니다: {e}"
                    meta = None

            st.markdown(answer)
            if meta:
                with st.expander("검색 상세 정보", expanded=False):
                    _render_search_detail(meta)

        st.session_state.messages.append(
            {"role": "assistant", "content": answer, "meta": meta}
        )

    if st.button("대화 초기화", use_container_width=False):
        st.session_state.messages = []
        st.rerun()


# ════ Tab 2: 문서 관리 ══════════════════════════════════════

with tab_docs:
    st.header("문서 관리")

    col_upload, col_list = st.columns([1, 1], gap="large")

    with col_upload:
        st.subheader("문서 업로드")
        uploaded = st.file_uploader(
            "파일을 업로드하세요",
            type=["pdf", "docx", "hwpx", "md", "txt"],
            help="지원 형식: PDF, DOCX, HWPX, MD, TXT",
        )
        if uploaded and st.button("인덱싱 시작", type="primary", use_container_width=True):
            try:
                resp = requests.post(
                    f"{INDEXER_URL}/documents",
                    files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
                    timeout=30,
                )
                resp.raise_for_status()
                st.success(f"'{uploaded.name}' 인덱싱이 시작되었습니다.")
                st.caption("완료되면 문서 목록에서 확인하세요.")
            except requests.exceptions.ConnectionError:
                st.error("인덱서 서버에 연결할 수 없습니다.")
            except Exception as e:
                st.error(f"오류: {e}")

        # 인덱싱 진행 중인 작업 상태 표시
        if st.session_state.get("indexing_jobs"):
            st.subheader("진행 중인 인덱싱")
            done_jobs = []
            for job_id, job_name in st.session_state.indexing_jobs.items():
                try:
                    r = requests.get(f"{INDEXER_URL}/documents/{job_id}/status", timeout=5)
                    status = r.json()
                    step = status.get("step", "")
                    s = status.get("status", "")
                    if s == "completed":
                        st.success(f"✓ {job_name} 완료 ({status.get('chunk_count', 0)}개 청크)")
                        done_jobs.append(job_id)
                    elif s == "failed":
                        st.error(f"✗ {job_name} 실패: {step}")
                        done_jobs.append(job_id)
                    else:
                        st.info(f"⏳ {job_name}: {step}")
                except Exception:
                    pass
            for job_id in done_jobs:
                del st.session_state.indexing_jobs[job_id]

    with col_list:
        st.subheader("인덱싱된 문서 목록")
        if st.button("새로고침", use_container_width=True):
            st.rerun()

        try:
            resp = requests.get(f"{INDEXER_URL}/documents", timeout=10)
            resp.raise_for_status()
            docs = resp.json().get("documents", [])

            if not docs:
                st.info("인덱싱된 문서가 없습니다.")
            else:
                for doc in docs:
                    with st.container(border=True):
                        c1, c2 = st.columns([4, 1])
                        with c1:
                            st.markdown(f"**{doc.get('title', '제목 없음')}**")
                            st.caption(
                                f"{doc.get('domain_category', '')} "
                                f"| {doc.get('doc_type', '')} "
                                f"| 청크 {doc.get('chunk_count', '?')}개"
                            )
                            if doc.get("keywords"):
                                kws = doc["keywords"][:5]
                                st.caption("키워드: " + ", ".join(kws))
                            st.caption(f"ID: `{doc.get('document_id', '')[:8]}...`")
                        with c2:
                            doc_id = doc.get("document_id", "")
                            if st.button("삭제", key=f"del_{doc_id}", type="secondary"):
                                try:
                                    r = requests.delete(
                                        f"{INDEXER_URL}/documents/{doc_id}", timeout=10
                                    )
                                    r.raise_for_status()
                                    st.success("삭제 완료")
                                    time.sleep(0.5)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"삭제 실패: {e}")
        except requests.exceptions.ConnectionError:
            st.error("인덱서 서버에 연결할 수 없습니다.")
        except Exception as e:
            st.error(f"목록 조회 오류: {e}")
