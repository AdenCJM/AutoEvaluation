.PHONY: run setup setup-defaults dashboard clean

# Start the optimisation loop (installs deps, validates key, runs loop)
run:
	./start.sh

# Interactive setup wizard
setup:
	python3 setup.py

# Skip-all-prompts setup (requires API key in .env or environment)
setup-defaults:
	python3 setup.py --defaults

# Start the live dashboard
dashboard:
	python3 tools/dashboard_server.py

# Clean intermediate files (keeps config + results)
clean:
	rm -rf .tmp/ __pycache__/ tools/__pycache__/
