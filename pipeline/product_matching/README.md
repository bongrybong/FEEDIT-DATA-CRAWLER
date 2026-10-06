# FEEDIT Product Matching v1

Brand 하나 안에서만 ProductSource -> FEEDIT Product 후보를 계산하는 read-only 파이프라인.

현재:
- 같은 Brand의 Product만 후보
- 같은 Brand의 미매핑 ProductSource만 대상
- normalized_name 비교만 구현
- Product 자체 이름 + 기존 연결 ProductSource 이름 비교
- Top K 반환
- DB write 없음
- 자동 매핑 없음
- 이미지 없음

다음 확장:
- style_no
- category
- DictionaryTerm
- normalized attributes
