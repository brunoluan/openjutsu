# Deploy na Nuvem (Mais Barato para 1 Vídeo/Dia de até 30s)

Esta pasta contém a automação completa para rodar o **Openjutsu** na nuvem pagando o mínimo possível — ideal para gerar **1 vídeo por dia de até 30 segundos** para marketing digital.

---

## Comparativo de Custos (1 vídeo/dia de 30s)

| Opção | GPU Recomendada | Custo Computação | Armazenamento (~55 GB) | Custo Total Estimado / Mês |
| :--- | :--- | :--- | :--- | :--- |
| **1. Modal.com (Recomendado)** | NVIDIA **L4** (24 GB VRAM, 64 GB RAM) | ~\$0,80/h GPU + CPU/RAM (~15–18 h/mês) | ~\$4,95/mês (`modal.Volume`) | **\$0/mês** (coberto pelos **\$30/mês gratuitos** do plano Starter!) |
| **2. Vast.ai (Mais barato pago)** | **RTX 3090** (24 GB VRAM, 64 GB RAM) | ~\$0,15–\$0,22/h (~15–20 h/mês = ~\$3,50) | ~\$7–\$10/mês (pausado) ou \$0 (recriando) | **~\$11–\$14/mês** |
| **3. RunPod (Network Volume)** | **RTX 3090 / 4090** (24 GB VRAM, 64 GB RAM) | ~\$0,22–\$0,59/h (~15 h/mês = ~\$5–\$9) | ~\$7,00/mês (`100 GB` Volume) | **~\$12–\$16/mês** |

> [!TIP]
> **Por que o Modal sai praticamente grátis?** O plano Starter do Modal dá **\$30/mês em créditos gratuitos** todo mês e desliga a GPU automaticamente (`scaledown_window=180` = 3 min sem uso). Você nunca esquece a máquina ligada de madrugada.

---

## Opção 1A: Modal.com 100% pelo Navegador (GitHub Actions — Sem instalar nada no PC)

Você **não precisa instalar Python, Node, Git nem CLI** no seu computador. Tudo roda pelo GitHub Actions usando o workflow [`.github/workflows/deploy-modal.yml`](../.github/workflows/deploy-modal.yml).

### O que você faz uma única vez:

1. **Criar conta no [Modal.com](https://modal.com)** (plano Starter gratuito, **\$30/mês** grátis; exige cartão para liberar GPU — configure um limite de gasto no painel se desejar).
2. **Gerar um API Token no Modal**:
   - No painel do Modal, acesse **Settings → API Tokens → New Token**.
   - Copie os dois valores exibidos: `MODAL_TOKEN_ID` e `MODAL_TOKEN_SECRET`.
3. **Cadastrar os Secrets neste repositório no GitHub**:
   - No GitHub, abra **Settings → Secrets and variables → Actions → New repository secret** e crie 3 secrets:
     - `MODAL_TOKEN_ID`: cole o ID do token do Modal
     - `MODAL_TOKEN_SECRET`: cole o secret do token do Modal
     - `OPENJUTSU_PASSWORD`: crie uma senha forte com **12 ou mais caracteres** (será a senha para entrar no site do Openjutsu)
4. **Executar o deploy com 1 clique**:
   - No GitHub, abra a aba **Actions → Deploy to Modal → Run workflow → Run workflow**.
   - O workflow baixa os ~51 GB de modelos no Volume persistente do Modal (em CPU barata) e publica a URL HTTPS.
   - Ao terminar, clique no job concluído (**Summary**) para ver a **URL fixa** (`https://...modal.run`).
   - Entre com usuário **`openjutsu`** e a senha de `OPENJUTSU_PASSWORD`.

---

## Opção 1B: Modal.com via Terminal Local (Alternativa)

Se preferir rodar do seu próprio terminal:

```bash
pip install modal
modal setup
modal secret create --force openjutsu-auth OPENJUTSU_PASSWORD="SuaSenhaForteAqui123!"
modal run deploy/modal_app.py
modal deploy deploy/modal_app.py
```

> [!IMPORTANT]
> **Ao renderizar vídeos de 30s no Modal**: mantenha a aba do navegador aberta enquanto o vídeo estiver gerando (a barra de progresso envia reconexões automáticas a cada poucos segundos, mantendo o container ativo até o download terminar). Se quiser usar uma GPU 2× mais rápida (**L40S 48 GB**), selecione `L40S` no workflow do GitHub Actions ou rode:
> `OPENJUTSU_MODAL_GPU=L40S modal deploy deploy/modal_app.py`

---

## Opção 2: Vast.ai ou RunPod (GPU Alugada + Auto-Stop)

Use esta opção se ultrapassar os \$30/mês gratuitos do Modal ou preferir **RTX 3090 / RTX 4090** dedicada:

1. Alugue uma instância com:
   - **GPU**: RTX 3090 (24 GB) ou RTX 4090 (24 GB) — mínimo 16 GB VRAM.
   - **RAM do sistema**: **64 GB** ou mais (obrigatório para streaming do MiniMax H3 INT8).
   - **Disco**: **100 GB+** em `/workspace`.
   - **Template**: PyTorch / Ubuntu 22.04 ou 24.04.

2. No terminal da máquina, clone este repositório e execute o instalador:
   ```bash
   git clone https://github.com/brunoluan/openjutsu.git /workspace/openjutsu
   cd /workspace/openjutsu
   bash deploy/cloud_setup.sh
   ```
   *(Flags opcionais: `--fast-describer`, `--with-picture`, `--with-face-fix`)*

3. Inicie tudo com **HTTPS automático (Cloudflare Tunnel)** e **desligamento automático após 20 min ocioso**:
   ```bash
   bash deploy/start_cloud.sh
   ```
   O terminal mostrará a URL `https://....trycloudflare.com`, o usuário `openjutsu` e a senha gerada em `.env.local`.

---

## Dicas para Economizar GPU em Vídeos de Marketing de 30s

1. **Valide os primeiros 5 segundos antes de renderizar 30s**: use **Part to use → Length: 5s (Start)** para testar se o personagem, iluminação e movimento ficaram perfeitos antes de gastar GPU nos 30 segundos completos.
2. **Use `Check mask` primeiro**: leva ~1 minuto e mostra exatamente a área que será regenerada, evitando renderizações erradas.
3. **Resolução ideal para Reels / TikTok / Shorts**: **736p** (padrão) ou **480p** (2× mais rápido; ótimo para testar ganchos/hooks). Evite 1080p na geração (faça upscale depois se precisar).
4. **Licença comercial**: não ative `--with-face-fix` para anúncios pagos sem revisar a licença não-comercial do InsightFace (`buffalo_l`). Os modos **Simple**, **Advanced**, **Object swap** e **Fix colours** não dependem do InsightFace.
