local global = vim.g
local o = vim.opt
local d = vim.diagnostic

o.number = true
o.relativenumber = true
o.clipboard = "unnamedplus"
o.syntax = "on"
o.autoindent = true
o.cursorline = true
o.expandtab = true
o.shiftwidth = 2
o.tabstop = 2
o.encoding = "UTF-8"
o.ruler = true
o.mouse = "a"
o.title = true
o.hidden = true
o.ttimeoutlen = 0
o.wildmenu = true
o.showcmd = true
o.showmatch = true
o.inccommand = "split"
o.splitright = true
o.splitbelow = true
o.termguicolors = true
o.scrolloff = 10

d.config({
	float = {
		border = "rounded",
		source = "always",
	},
})

d.config({
	virtual_text = {
		spacing = 4,
		prefix = "●",
	},
})
