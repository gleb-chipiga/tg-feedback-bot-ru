all:
	make isort
	make flake
	make mypy
	make cov
isort:
	isort *.py */*.py
flake:
	flake8 .
mypy:
	mypy .
test:
	python -m pytest tests
cov:
	python -m pytest --cov=feedback_bot --cov-report html tests
build:
	rm -rf dist
	python setup.py sdist
	python setup.py bdist_wheel
	rm -rf tg_feedback_bot_ru.egg-info
	rm -rf build
clean:
	rm -rf __pycache__
	rm -rf .mypy_cache
	rm -rf .pytest_cache
	rm - rf .hypothesis
	rm -rf htmlcov
	rm .coverage
	rm -rf tg_feedback_bot_ru.egg-info
	rm -rf dist
	rm -rf buld
