vim.loader.enable() -- faster startup by caching complied lua modules

vim.schedule(function()
	vim.o.clipboard = "unnamedplus"
end)

vim.o.cmdheight = 1
vim.o.encoding = "UTF-8"
vim.o.number = true -- line numbers
vim.o.completeopt = "menuone,noinsert,noselect"
vim.opt.fillchars = { eob = " " } -- fill characters in empty lines
vim.o.autoread = true -- auto-read changes from outside of neovim
vim.o.relativenumber = true
vim.o.ruler = true -- show cursor line and column
vim.o.syntax = "on"
vim.o.mouse = "a"
vim.o.shiftwidth = 2
vim.o.breakindent = true -- wrapped line has the same indentation as first line
vim.o.undofile = true
vim.o.ignorecase = true -- case insensitive search
vim.o.smartcase = true -- case sensitive search when one or more capital letters used
vim.o.signcolumn = "yes:2" -- line on the left side for errors and git status
vim.o.colorcolumn = "100"
vim.opt.textwidth = 100
vim.opt.linebreak = true
vim.o.updatetime = 250 -- swap file update time
vim.o.timeoutlen = 300 -- time in milliseconds to wait for a mapped sequence to complete
-- propably won't use splitting
vim.o.splitright = true
vim.o.splitbelow = true
vim.o.list = true
vim.opt.listchars = { tab = "» ", trail = "·", nbsp = "␣" } -- display trailing whitespaces with characters
-- what is substitute
vim.o.inccommand = "split" -- show substitute in a window
vim.o.cursorline = true -- highlight current line
vim.o.scrolloff = 10 -- minimal number of lines to keep above and bellow cursor
vim.o.confirm = true

vim.diagnostic.config({
	float = {
		border = "rounded",
		source = "always",
	},
})

-- highlight yanked text
vim.api.nvim_create_autocmd("TextYankPost", {
	desc = "Highlight when yanking (copying) text",
	group = vim.api.nvim_create_augroup("kickstart-highlight-yank", { clear = true }),
	callback = function()
		vim.hl.on_yank()
	end,
})
