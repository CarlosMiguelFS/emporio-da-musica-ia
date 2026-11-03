# Store information from the policy manual

Generated with the real agent (`main.py`) on 03/11/2026 08:10.

Lines starting with `> tool:` show which tools the agent called (they are not shown to the customer).

**Customer:** Qual o endereço da loja?

> tool: `search_policies(question='endereço da loja')`

**Agent:** Estamos localizados na Rua 14 de Maio, 3200, no Centro de Campo Grande. Se precisar de mais alguma informação, estamos à disposição!

**Customer:** e no sábado vocês abrem que horas?

> tool: `search_policies(question='horário de funcionamento sábado')`

**Agent:** Aos sábados, nossa loja funciona das 09:00 às 13:00. Se precisar de algo, será um prazer te receber por lá!

**Customer:** aceita pix?

> tool: `search_policies(question='formas de pagamento pix')`

**Agent:** Aceitamos PIX sim! Para pagamentos à vista via PIX, você ganha 5% de desconto sobre o preço de tabela, exceto em produtos que já estejam em promoção.  
  
Gostaria de ver algum instrumento específico hoje?
