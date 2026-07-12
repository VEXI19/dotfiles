vim.g.mapleader = " "

local function map(mode, lhs, rhs, opts)
	opts = opts or {}
	opts.silent = true
	vim.keymap.set(mode, lhs, rhs, opts)
end

-- Save
map("n", "<leader>w", "<CMD>w<CR>")

-- Quit
map("n", "<leader>q", "<CMD>q<CR>")

-- Exit insert mode
map("i", "jk", "<ESC>")

-- Find and replace
map("n", "<teader>fr", [[:%s/\<<C-r><C-w>\>//gc<Left><Left><Left>]], { desc = "Replace word under cursor" })
map("v", "<leader>fr", [["hy:%s/<C-r>h/<C-r>h/gI<Left><Left><left>]])

-- File navigation
map("n", "<C-p>", ":bp<CR>")
map("n", "<C-n>", ":bn<CR>")
map("n", "<leader><leader>", "<C-^>", { desc = "Toggle last file" })

-- Rename variable
map("n", "<leader>rn", vim.lsp.buf.rename, { desc = "Rename symbol" })

-- Lsp hover
map("n", "<leader>k", vim.lsp.buf.hover, { buffer = bufnr })

-- NeoTree
map("n", "<leader>h", "<CMD>Neotree toggle<CR>")

-- New Windows
map("n", "<leader>o", "<CMD>vsplit<CR>")
map("n", "<leader>p", "<CMD>split<CR>")

-- Window Navigation
map("n", "<C-h>", "<C-w>h")
map("n", "<C-l>", "<C-w>l")
map("n", "<C-k>", "<C-w>k")
map("n", "<C-j>", "<C-w>j")

-- Resize Windows
map("n", "<A-h>", "<C-w>>")
map("n", "<A-l>", "<C-w><")
map("n", "<A-k>", "<C-w>-")
map("n", "<A-j>", "<C-w>+")
-- map("n", "<C-Left>", "<C-w>>")
-- map("n", "<C-Right>", "<C-w><")
-- map("n", "<C-Up>", "<C-w>-")
-- map("n", "<C-Down>", "<C-w>+")

-- Telescope
map("n", "<leader>ff", "<CMD>Telescope find_files<CR>", { desc = "Fuzzy find files in cwd" })
map("n", "<leader>fg", "<CMD>Telescope live_grep<CR>", { desc = "Find string in cwd" })
map("n", "<leader>fb", "<CMD>Telescope buffers<CR>", { desc = "Fuzzy find opened files" })
map("n", "<leader>fs", "<CMD>Telescope git_status<CR>", { desc = "Show git file diffs" })
map("n", "<leader>fc", "<CMD>Telescope git_commits<CR>", { desc = "Browse git commits" })
map("n", "gd", "<cmd>Telescope lsp_definitions<CR>")
map("n", "gi", "<cmd>Telescope lsp_references<CR>")

-- Errors
map("n", "<leader>e", vim.diagnostic.open_float)
map("n", "<leader>[", vim.diagnostic.goto_prev)
map("n", "<leader>]", vim.diagnostic.goto_next)

-- List actions
map("n", "<leader>ca", vim.lsp.buf.code_action)
