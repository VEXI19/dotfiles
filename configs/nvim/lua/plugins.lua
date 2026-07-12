vim.pack.add({
  { src = "https://github.com/lewis6991/gitsigns.nvim" },
  { src = "https://github.com/neovim/nvim-lspconfig" },
	-- neotree and dependencies
	{ src = "https://github.com/nvim-neo-tree/neo-tree.nvim" },
	{ src = "https://github.com/nvim-lua/plenary.nvim" },
	{ src = "https://github.com/MunifTanjim/nui.nvim" },
	{ src = "https://github.com/nvim-tree/nvim-web-devicons" },

	{ src = "https://github.com/EdenEast/nightfox.nvim" },
	{ src = "https://github.com/catppuccin/nvim", name = "catppuccin" },

	{ src = "https://github.com/mason-org/mason.nvim" }
})

require("mason").setup()

vim.lsp.enable({
  "stylua"
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
-- require("catppuccin").setup({
-- 	flavour = "auto", -- latte, frappe, macchiato, mocha
-- 	transparent_background = true,
-- })
vim.cmd("colorscheme nightfox")
