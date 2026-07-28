vim.pack.add({
	-- github line changes
	{ src = "https://github.com/lewis6991/gitsigns.nvim" },

	-- autocomplete
	{ src = "https://github.com/hrsh7th/nvim-cmp" },
	{ src = "https://github.com/hrsh7th/cmp-nvim-lsp" },
	{ src = "https://github.com/hrsh7th/cmp-buffer" },
	{ src = "https://github.com/hrsh7th/cmp-path" },
	{ src = "https://github.com/hrsh7th/cmp-emoji" },
	{ src = "https://github.com/onsails/lspkind.nvim" },

	-- snippets
	{ src = "https://github.com/L3MON4D3/LuaSnip" },
	{ src = "https://github.com/saadparwaiz1/cmp_luasnip" },
	{ src = "https://github.com/rafamadriz/friendly-snippets" },

	-- neotree and dependencies
	{ src = "https://github.com/nvim-neo-tree/neo-tree.nvim" },
	{ src = "https://github.com/nvim-lua/plenary.nvim" },
	{ src = "https://github.com/MunifTanjim/nui.nvim" },
	{ src = "https://github.com/nvim-tree/nvim-web-devicons" },

	-- themes
	{ src = "https://github.com/EdenEast/nightfox.nvim" },

	-- lsp shit
	{ src = "https://github.com/neovim/nvim-lspconfig" },
	{ src = "https://github.com/mason-org/mason.nvim" },
	{ src = "https://github.com/mason-org/mason-lspconfig.nvim" },
	{ src = "https://github.com/WhoIsSethDaniel/mason-tool-installer.nvim" },
})

require("neo-tree").setup({
	filesystem = {
		filtered_items = {
			visible = true,
			hide_dotfiles = false,
		},
	},
})

require("nightfox").setup({
	options = {
		transparent = true,
		terminal_colors = true,
	},
})
vim.cmd("colorscheme nightfox")

require("gitsigns").setup({
	sign_priority = 100, -- Default is 6. Higher number = rendered first on the left
})

vim.diagnostic.config({
	signs = {
		priority = 20, -- Keep LSP signs lower priority than gitsigns
	},
})
