.PHONY: up down init test run lint

up:          ## MySQL 기동
	docker compose up -d --wait

down:
	docker compose down -v

reset:       ## 전부 지우고 데모 상태로
	python -m app.bootstrap --reset

init:        ## 스키마 생성
	python -m app.bootstrap

test:        ## 전체 테스트 (동시성 재현 테스트 포함)
	pytest -v

run:         ## API 서버
	uvicorn app.main:app --reload

worker:      ## 만료 해제 워커
	python -m app.workers.expire_holds

lint:
	ruff check app tests
