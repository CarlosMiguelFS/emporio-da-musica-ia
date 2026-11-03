# Store information from the policy manual

Generated with the real agent (`main.py`) on 02/11/2026 23:33.

Lines starting with `> tool:` show which tools the agent called (they are not shown to the customer).

**Customer:** Qual o endereço da loja?

> tool: `search_policies(question='endereço da loja')`

**Agent:** Nossa loja fica na Rua 14 de Maio, 3200, no Centro de Campo Grande. Se passar por lá, vai ser um prazer te receber! Você está procurando algum instrumento específico hoje?

**Customer:** e no sábado vocês abrem que horas?

> tool: `search_policies(question='horário de funcionamento sábado')`

**Agent:** Aos sábados, a gente abre das 09:00 às 13:00. Como hoje é feriado, nossa equipe está descansando, mas amanhã cedo já voltamos com tudo. Quer que eu te ajude a ver algum instrumento para quando a gente abrir?

**Customer:** aceita pix?

> tool: `search_policies(question='formas de pagamento pix')`

**Agent:** Aceitamos PIX sim, e o melhor é que você ganha 5% de desconto sobre o preço de tabela pagando à vista dessa forma. Se preferir parcelar, também temos opções no cartão de crédito em até 12 vezes sem juros. Já tem algum instrumento em mente que gostaria de conferir o valor?
