local cmp = require("cmp")
local cmp_nvim_lsp = require("cmp_nvim_lsp")
local luasnip = require("luasnip")
local lspkind = require("lspkind")

-- Load snippet packs only for the languages matching your mason tools
require("luasnip.loaders.from_vscode").load({
	include = {
		"lua",
		"python",
		"c",
		"cpp",
		"css",
		"tailwindcss",
		"javascript",
		"typescript",
		"javascriptreact",
		"typescriptreact",
		"go",
		"html",
		"json",
	},
})

-- Capabilities to pass to every LSP server
local capabilities = vim.lsp.protocol.make_client_capabilities()
capabilities = vim.tbl_deep_extend("force", capabilities, cmp_nvim_lsp.default_capabilities())

cmp.setup({
	-- Snippet engine
	snippet = {
		expand = function(args)
			luasnip.lsp_expand(args.body)
		end,
	},

	-- Popup behaviour
	completion = {
		completeopt = "menu,menuone,preview",
	},

	-- Kind icons in the completion menu
	formatting = {
		format = lspkind.cmp_format({
			mode = "symbol_text",
			maxwidth = 50,
			ellipsis_char = "...",
			show_labelDetails = true,
		}),
	},

	-- Keybindings
	mapping = cmp.mapping.preset.insert({
		-- Scroll documentation window
		["<C-b>"] = cmp.mapping.scroll_docs(-4),
		["<C-f>"] = cmp.mapping.scroll_docs(4),

		-- Manually trigger / abort
		["<C-j>"] = cmp.mapping.complete(),
		["<C-e>"] = cmp.mapping.abort(),

		-- Navigate items
		["<C-n>"] = cmp.mapping.select_next_item(),
		["<C-p>"] = cmp.mapping.select_prev_item(),

		-- Confirm selection
		["<CR>"] = cmp.mapping.confirm({ select = true }),
		["<C-y>"] = cmp.mapping.confirm({ select = true }),
	}),

	-- Completion sources (higher priority first)
	sources = cmp.config.sources({
		{ name = "nvim_lsp" },
		{ name = "luasnip" },
		{ name = "buffer" },
		{ name = "path" },
		{ name = "emoji" },
	}),
})

return capabilities
