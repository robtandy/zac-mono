.PHONY: setup build dev clean

setup: ## Install all dependencies
	uv sync
	cd packages/tui && npm install
	cd packages/web && npm install
	playwright install chromium

build: ## Build web UI
	cd packages/web && npm run build

dev: setup build ## Full setup + build (run this after cloning)

clean: ## Remove generated files
	rm -rf packages/web/dist
	rm -rf packages/tui/node_modules
	rm -rf packages/web/node_modules
