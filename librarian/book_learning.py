"""
도서 학습: 파일을 Gemini에 넘겨서 내용 설명을 받고 book_knowledge에 저장
"""

import os
import logging
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, FILES_DIR, GEMINI_MODEL

logger = logging.getLogger("BookLearning")

# 지원 MIME 타입
MIME_MAP = {
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
}


async def learn_book(librarian_db, book_id: int, title: str, filename: str, stored_name: str):
    """책 파일을 Gemini에 넘겨서 내용을 학습하고 book_knowledge에 저장"""

    # 이미 학습했으면 건너뜀
    if await librarian_db.has_book_knowledge(book_id):
        logger.info(f"도서 학습 건너뜀 (이미 있음): 《{title}》")
        return

    file_path = os.path.join(FILES_DIR, stored_name)
    if not os.path.exists(file_path):
        logger.warning(f"도서 학습 실패 (파일 없음): 《{title}》 → {file_path}")
        return

    ext = os.path.splitext(filename)[1].lower()
    mime_type = MIME_MAP.get(ext)
    if not mime_type:
        logger.info(f"도서 학습 건너뜀 (미지원 형식): 《{title}》 ({ext})")
        return

    try:
        with open(file_path, "rb") as f:
            data = f.read()

        logger.info(f"도서 학습 시작: 《{title}》 ({len(data):,} bytes)")

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Content(role="user", parts=[
                    types.Part.from_bytes(data=data, mime_type=mime_type),
                    types.Part.from_text(text=(
                        f"이 책의 제목은 《{title}》이다. "
                        "사서가 이 책의 내용을 숙지할 수 있도록 상세하게 설명해. "
                        "핵심 주장, 주요 개념, 챕터별 내용, 인상적인 구절이나 수치를 빠짐없이 포함해."
                    )),
                ]),
            ],
            config=types.GenerateContentConfig(
                max_output_tokens=8192,
                temperature=0.3,
            ),
        )

        result = ""
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.text:
                    result += part.text

        if result:
            await librarian_db.save_book_knowledge(book_id, result, source=title)
            logger.info(f"도서 학습 완료: 《{title}》 ({len(result):,}자)")
        else:
            logger.warning(f"도서 학습 실패 (빈 응답): 《{title}》")

    except Exception as e:
        logger.error(f"도서 학습 실패: 《{title}》 → {e}")
