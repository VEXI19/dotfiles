vim.loader.enable() -- faster startup by caching complied lua modules

vim.schedule(function()
	vim.o.clipboard = "unnamedplus"
end)

vim.o.cmdheight = 1
vim.o.encoding = "UTF-8"
vim.o.number = true -- line numbers
vim.o.completeopt = "menuone,noinsert,preview"
-- vim.opt.complete:append("o") -- use fuzy func for autocomplete
vim.opt.pumheight = 6
vim.opt.fillchars = { eob = " " } -- fill characters in empty lines
vim.o.autoread = true -- auto-read changes from outside of neovim
vim.o.relativenumber = true
vim.o.ruler = true -- show cursor line and column
vim.o.syntax = "on"
vim.o.mouse = "a"
vim.o.shiftwidth = 4
vim.o.tabstop = 4
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

-- show only most severe lsp info
local orig_signs_handler = vim.diagnostic.handlers.signs

vim.diagnostic.handlers.signs = {
	show = function(namespace, bufnr, diagnostics, opts)
		-- Group diagnostics by line number and select the highest severity (lowest number)
		local highest_severity_per_line = {}

		for _, d in ipairs(diagnostics) do
			local existing = highest_severity_per_line[d.lnum]
			-- Lower number = higher severity (ERROR = 1, WARN = 2, INFO = 3)
			if not existing or d.severity < existing.severity then
				highest_severity_per_line[d.lnum] = d
			end
		end

		local filtered = {}
		for _, d in pairs(highest_severity_per_line) do
			table.insert(filtered, d)
		end

		orig_signs_handler.show(namespace, bufnr, filtered, opts)
	end,
	hide = function(namespace, bufnr)
		orig_signs_handler.hide(namespace, bufnr)
	end,
}
-- format on save
vim.api.nvim_create_autocmd("BufWritePre", {
	pattern = "*",
	callback = function(args)
		vim.lsp.buf.format({
			bufnr = args.buf,
			timeout_ms = 2000,
		})
	end,
})

vim.api.nvim_create_autocmd("LspAttach", {
	group = vim.api.nvim_create_augroup("my.lsp", {}),
	callback = function(ev)
		local client = assert(vim.lsp.get_client_by_id(ev.data.client_id))
		if client:supports_method("textDocument/completion") then
			-- Optional: trigger autocompletion on EVERY keypress. May be slow!
			local chars = {}
			for i = 32, 126 do
				table.insert(chars, string.char(i))
			end
			client.server_capabilities.completionProvider.triggerCharacters = { "o" }
			vim.lsp.completion.enable(true, client.id, ev.buf, { autotrigger = true })
		end
	end,
})
