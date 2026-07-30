local capabilities = require("completion") -- from lua/completion.lua

-- ── Per-server config using Neovim 0.11+ native API ──────────────────────────
-- These are registered BEFORE mason-lspconfig's auto-enable runs, so
-- vim.lsp.enable() picks up our custom settings.

vim.lsp.config("lua_ls", {
	capabilities = capabilities,
	settings = {
		Lua = {
			runtime = { version = "LuaJIT" },
			diagnostics = {
				globals = { "vim", "require" },
			},
			workspace = {
				library = vim.api.nvim_get_runtime_file("", true),
			},
			telemetry = { enable = false },
		},
	},
})

vim.lsp.config("jsonls", {
	capabilities = capabilities,
	settings = {
		json = {
			schemas = {
				{
					fileMatch = { "tsconfig.json", "tsconfig.*.json" },
					url = "https://json.schemastore.org/tsconfig.json",
				},
			},
			validate = { enable = true },
		},
	},
})

require("mason").setup()
-- mason-lspconfig auto-enables installed servers via vim.lsp.enable()
require("mason-lspconfig").setup()

require("mason-tool-installer").setup({
	ensure_installed = {
		"lua-language-server",
		"stylua",
		"black",
		"clangd",
		"css-lsp",
		"eslint-lsp",
		"eslint_d",
		"golangci-lint",
		"golangci-lint-langserver",
		"gopls",
		"html-lsp",
		"isort",
		"json-lsp",
		"prettier",
		"pylint",
		"pyright",
		"tailwindcss-language-server",
		"vtsls",
	},
})
