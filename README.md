# 국내주식 분석 앱 (PWA)

시가총액 상위 종목의 일봉 데이터를 매일 장 마감 후 분석하고, 규칙 기반으로 오늘의 추천 종목 하나를 보여주는 모바일 웹앱입니다.

## 구성
- `scripts/update_data.py` : KRX 일봉 수집(pykrx), 지표 계산, 추천 산출 → `docs/data.json`
- `docs/` : 모바일 화면(PWA). 홈 화면에 추가하면 앱처럼 사용 가능
- `.github/workflows/daily.yml` : 평일 16:10 KST에 자동 갱신 후 커밋

## 계산 항목
- 지표: 20일·60일 이동평균, RSI(14), MACD 히스토그램, 거래량 비율, 60일 수익률
- 추천 조건 6개 중 5개 이상 충족한 종목 중 최고 점수 1개 (동점이면 60일 수익률이 높은 종목)
- 예상 범위: 최근 20일 변동성 기준 5거래일 80% 구간 (정규분포 가정의 추정치)

## 설치 방법
1. GitHub에 새 저장소를 만들고 이 폴더를 push합니다.
2. 저장소 Settings → Pages에서 Source를 `Deploy from a branch`, 브랜치 `main`, 폴더 `/docs`로 설정합니다.
3. Actions 탭에서 `daily-update`를 한 번 수동 실행(Run workflow)해 첫 데이터를 만듭니다.
4. 휴대폰 브라우저에서 Pages 주소를 열고 "홈 화면에 추가"를 선택합니다.

로컬에서 테스트하려면 `pip install -r requirements.txt` 후 `python scripts/update_data.py`를 실행하고, `docs/` 폴더를 정적 서버로 열면 됩니다.

## 주의
- 이 앱은 투자 권유가 아닙니다. 추천은 정해진 규칙의 결과일 뿐이며 예측 정확도는 검증되지 않았습니다.
- pykrx는 KRX 웹 데이터를 가져오므로 KRX 정책이 바뀌면 동작하지 않을 수 있습니다. 실패 시 Actions 로그를 확인하세요.
- 백테스트(과거 추천의 실제 성과 검증)는 아직 포함되지 않았습니다. `docs/history.json`에 이력이 쌓이므로 다음 단계에서 추가할 수 있습니다.
