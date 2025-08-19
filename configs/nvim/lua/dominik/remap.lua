vim.keymap.set("n", "<leader>pv", vim.cmd.Ex)

-- MOVE REMAPS --
vim.keymap.set({'v', 'n'}, '<C-H>', '^')
vim.keymap.set({'v', 'n'}, '<C-L>', '$')
vim.keymap.set('i', '<C-H>', '<C-O>^')
vim.keymap.set('i', '<C-L>', '<C-O>$')

vim.keymap.set('i', '{{', '{}<Left><Enter><Enter><Up>', { noremap = true, silent = true })
vim.keymap.set('i', '[[', '[]<Left><Enter><Enter><Up>', { noremap = true, silent = true })

vim.keymap.set('i', '<C-BS>', '<C-w>', { noremap = true, silent = true })
