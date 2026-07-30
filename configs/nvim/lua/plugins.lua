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

	-- telescope
	{ src = "https://github.com/nvim-telescope/telescope.nvim" },
	{ src = "https://github.com/nvim-telescope/telescope-fzf-native.nvim" },

	-- autopairs
	{ src = "https://github.com/windwp/nvim-autopairs" },
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

-- ── Telescope ──────────────────────────────────────────────────────────────────────
local telescope = require("telescope")
local actions = require("telescope.actions")

telescope.setup({
	defaults = {
		file_ignore_patterns = {
			"node_modules",
			".git/",
			".cache",
			"target/",
			"build/",
			"dist/",
			".next/",
			"__pycache__",
			"%.o$",
			"%.class$",
			".mypy_cache",
			".pytest_cache",
		},
		path_display = { "smart" },
		hidden = true,
		mappings = {
			i = {
				["<C-j>"] = actions.move_selection_next,
				["<C-k>"] = actions.move_selection_previous,
				["<C-q>"] = actions.send_selected_to_qflist + actions.open_qflist,
			},
		},
	},
	pickers = {
		find_files = {
			hidden = true,
			no_ignore = false,
		},
		live_grep = {
			additional_args = { "--hidden", "--glob=!.git/*" },
		},
		buffers = {
			sort_lastused = true,
			previewer = false,
			mappings = {
				i = {
					["<C-d>"] = actions.delete_buffer,
				},
			},
		},
		git_status = {
			additional_args = { "--untracked-files=all" },
		},
	},
	extensions = {
		fzf = {
			fuzzy = true,
			override_generic_sorter = true,
			override_file_sorter = true,
			case_mode = "smart_case",
		},
	},
})

-- Load fzf extension (pcatch in case compilation failed)
pcall(telescope.load_extension, "fzf")

-- ── Autopairs ──────────────────────────────────────────────────────────────────────
local autopairs = require("nvim-autopairs")

autopairs.setup({
	enable_check_bracket_line = false,
})

-- Keep cursor inside brackets when confirming cmp completions
local cmp_autopairs = require("nvim-autopairs.completion.cmp")
local cmp = require("cmp")
cmp.event:on("confirm_done", cmp_autopairs.on_confirm_done())
