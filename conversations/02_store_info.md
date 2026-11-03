# Store information from the policy manual

Generated with the real agent (`main.py`) on 03/11/2026 08:41.

Lines starting with `> tool:` show which tools the agent called (they are not shown to the customer).

**Customer:** Qual o endereço da loja?

> tool: `search_policies(question='endereço da loja')`

**Agent:** Estamos localizados na Rua 14 de Maio, 3200, no Centro de Campo Grande. A loja está fechada agora, mas abriremos hoje às 09:00. Se precisar de algo antes disso, pode deixar sua mensagem por aqui!

**Customer:** e no sábado vocês abrem que horas?

> tool: `search_policies(question='horário de funcionamento sábado')`

**Agent:** Aos sábados, nosso atendimento é das 09:00 às 13:00. Como hoje é terça-feira, abriremos daqui a pouco, às 09:00. Posso te ajudar com mais alguma informação sobre nossos instrumentos?

**Customer:** aceita pix?

> tool: `search_policies(question='formas de pagamento pix desconto')`

**Agent:** Aceitamos PIX sim! Para produtos que não estão em promoção, oferecemos 5% de desconto para pagamentos à vista via PIX. Se o item estiver em oferta, o valor promocional já é o final.  
  
Gostaria de ver algum instrumento específico hoje?
