local lspconfig = require("lspconfig")
local capabilities = require("completion") -- exported by lua/cmp.lua

require("mason").setup()
require("mason-lspconfig").setup({
	handlers = {
		-- Automatically called for every server mason-lspconfig knows about
		function(server_name)
			local opts = {
				capabilities = capabilities,
			}

			-- Per-server tweaks
			if server_name == "lua_ls" then
				opts.settings = {
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
				}
			end

			lspconfig[server_name].setup(opts)
		end,
	},
})

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
		"vtsls",
	},
})
