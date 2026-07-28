require("mason").setup()
require("mason-lspconfig").setup()
require("mason-tool-installer").setup({
	ensure_installed = {
		"lua_ls",
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
		"lua-language-server",
		"prettier",
		"pylint",
		"pyright",
		"stylua",
		"tailwindcss-language-server",
		-- "typescript-language-server",
	},
})

vim.lsp.config("lua_ls", {
	settings = {
		Lua = {
			runtime = {
				version = "LuaJIT",
			},
			diagnostics = {
				globals = {
					"vim",
					"require",
				},
			},
			workspace = {
				library = vim.api.nvim_get_runtime_file("", true),
			},
			telemetry = {
				enable = false,
			},
		},
	},
})
