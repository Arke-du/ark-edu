
document.addEventListener("DOMContentLoaded", () => {
    const lista = document.getElementById("listaAlternativas");
    const botaoAdicionar = document.getElementById("adicionarAlternativa");
    const blocoAlternativas = document.getElementById("blocoAlternativas");
    const blocoVF = document.getElementById("blocoVerdadeiroFalso");
    const blocoResposta = document.getElementById("blocoRespostaEsperada");
    const blocoCriterios = document.getElementById("blocoCriterios");
    const blocoLinhasResposta = document.getElementById("blocoLinhasResposta");
    const disciplina = document.getElementById("disciplinaQuestao");
    const editor = document.getElementById("editorEnunciado");
    const enunciadoTexto = document.getElementById("enunciadoQuestao");
    const enunciadoHtml = document.getElementById("enunciadoHtml");
    const arquivoImagemEnunciado = document.getElementById("arquivoImagemEnunciado");
    const etapa = document.getElementById("etapaEnsino");
    const anoSerie = document.getElementById("anoSerie");
    const modalValidacao = document.getElementById("modalValidacao");
    const modalValidacaoTexto = document.getElementById("modalValidacaoTexto");
    const modalValidacaoOk = document.getElementById("modalValidacaoOk");
    let focoDepoisModal = null;
    function avisoArk(mensagem, foco=null){
        focoDepoisModal = foco;
        modalValidacaoTexto.textContent = mensagem;
        modalValidacao.classList.add("ativo");
        modalValidacaoOk.focus();
    }
    function fecharAvisoArk(){
        modalValidacao.classList.remove("ativo");
        if(focoDepoisModal){ setTimeout(()=>focoDepoisModal.focus(),30); focoDepoisModal=null; }
    }
    modalValidacaoOk.addEventListener("click", fecharAvisoArk);
    modalValidacao.addEventListener("click", e=>{ if(e.target===modalValidacao) fecharAvisoArk(); });

    const seriesPorEtapa = {
        "Educação Infantil":["Berçário","Maternal I","Maternal II","Pré I","Pré II"],
        "Ensino Fundamental - Anos Iniciais":["1º ano","2º ano","3º ano","4º ano","5º ano"],
        "Ensino Fundamental - Anos Finais":["6º ano","7º ano","8º ano","9º ano"],
        "Ensino Médio":["1ª série","2ª série","3ª série"],
        "EJA":["1º segmento","2º segmento","Ensino Médio"]
    };


    function normalizarTexto(valor){
        return String(valor || "")
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .replace(/º|ª/g, "")
            .replace(/\s+/g, " ")
            .trim()
            .toLowerCase();
    }

    function serieEquivalente(a, b){
        const na = normalizarTexto(a);
        const nb = normalizarTexto(b);
        if(!na || !nb) return false;
        if(na === nb) return true;
        const numeroA = na.match(/\d+/);
        const numeroB = nb.match(/\d+/);
        return numeroA && numeroB && numeroA[0] === numeroB[0];
    }

    function atualizarSeries(){
        const atual = anoSerie.dataset.valor || anoSerie.value;
        const itens = seriesPorEtapa[etapa.value] || [];
        anoSerie.innerHTML = '<option value="">Selecione</option>';
        itens.forEach(item => {
            const option = document.createElement("option");
            option.value = item;
            option.textContent = item;
            if(serieEquivalente(item, atual)) option.selected = true;
            anoSerie.appendChild(option);
        });
        anoSerie.dataset.valor = "";
    }

    function letra(indice){ return String.fromCharCode(65 + indice); }
    function atualizarAlternativas(){
        [...lista.querySelectorAll(".alternativa-item")].forEach((item, indice) => {
            item.querySelector(".alternativa-letra").textContent = letra(indice);
            const correta = item.querySelector(".input-correta");
            correta.value = String(indice);
            correta.type = document.querySelector('input[name="tipo_questao"]:checked').value === "multiplas_respostas" ? "checkbox" : "radio";
        });
    }
    function criarAlternativa(textoInicial="") {
        const indice = lista.children.length;
        const item = document.createElement("div");
        item.className = "alternativa-item";
        item.innerHTML = `<span class="alternativa-letra">${letra(indice)}</span><div class="alternativa-conteudo"><input class="alternativa-texto" type="text" name="alternativas[]" placeholder="Digite a alternativa" value=""><label class="upload-alternativa" title="Adicionar imagem" aria-label="Adicionar imagem"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m4 17 4.5-4.5 3.5 3 2.5-2.5 5.5 5"/></svg><input type="file" name="imagens_alternativas[]" accept="image/png,image/jpeg,image/webp"></label><div class="preview-wrap"><img class="preview-alternativa" alt="Pré-visualização"><button type="button" class="remover-imagem-alt">Remover imagem</button></div></div><div class="alternativa-acoes"><label class="marcar-correta"><input class="input-correta" type="radio" name="corretas[]" value="${indice}"> Correta</label><button type="button" class="remover-alternativa" title="Remover">×</button></div>`;
        item.querySelector(".alternativa-texto").value = textoInicial;
        const arquivo = item.querySelector('input[type="file"]');
        const previewWrap = item.querySelector(".preview-wrap");
        const preview = item.querySelector(".preview-alternativa");
        arquivo.addEventListener("change", () => {
            const selecionado = arquivo.files[0];
            if(selecionado){
                preview.src = URL.createObjectURL(selecionado);
                previewWrap.classList.add("ativo");
            } else {
                preview.removeAttribute("src");
                previewWrap.classList.remove("ativo");
            }
        });
        item.querySelector(".remover-imagem-alt").addEventListener("click", () => {
            arquivo.value = "";
            preview.removeAttribute("src");
            previewWrap.classList.remove("ativo");
        });
        item.querySelector(".remover-alternativa").addEventListener("click", () => {
            if(lista.children.length <= 2) return alert("A questão precisa ter pelo menos duas alternativas.");
            item.remove();
            atualizarAlternativas();
        });
        lista.appendChild(item);
        atualizarAlternativas();
        return item;
    }

    function inserirHtmlNoCursor(htmlInserido){
        editor.focus();
        document.execCommand("insertHTML", false, htmlInserido);
    }

    let figuraSelecionada = null;

    function limparSelecaoFigura(){
        editor.querySelectorAll("figure").forEach(figura => figura.classList.remove("selecionada"));
        figuraSelecionada = null;
    }

    function prepararFigura(figura){
        if(!figura || figura.dataset.preparada === "1") return;

        figura.dataset.preparada = "1";
        figura.contentEditable = "false";
        figura.draggable = true;
        figura.setAttribute("tabindex", "0");
        figura.setAttribute("aria-label", "Imagem do enunciado. Arraste para mover e use o canto inferior direito para redimensionar.");

        figura.addEventListener("click", evento => {
            evento.stopPropagation();
            limparSelecaoFigura();
            figura.classList.add("selecionada");
            figuraSelecionada = figura;
            figura.focus({preventScroll:true});
        });

        figura.addEventListener("dragstart", evento => {
            figura.classList.add("arrastando");
            figuraSelecionada = figura;
            evento.dataTransfer.effectAllowed = "move";
            evento.dataTransfer.setData("text/plain", figura.id || "imagem-enunciado");
        });

        figura.addEventListener("dragend", () => {
            figura.classList.remove("arrastando");
            editor.querySelectorAll(".editor-drop-line").forEach(linha => linha.remove());
        });
    }

    async function arquivoImagemOtimizado(arquivo){
        if(!arquivo) return null;
        if(arquivo.size > 20 * 1024 * 1024) throw new Error("A imagem original deve ter no máximo 20 MB.");
        const dataUrl = await new Promise((resolve, reject) => {
            const leitor = new FileReader();
            leitor.onload = () => resolve(leitor.result);
            leitor.onerror = () => reject(new Error("Não foi possível ler a imagem."));
            leitor.readAsDataURL(arquivo);
        });
        const img = await new Promise((resolve, reject) => {
            const imagem = new Image();
            imagem.onload = () => resolve(imagem);
            imagem.onerror = () => reject(new Error("Imagem inválida."));
            imagem.src = dataUrl;
        });
        const maxDim = 1600;
        const escala = Math.min(1, maxDim / Math.max(img.naturalWidth || 1, img.naturalHeight || 1));
        const largura = Math.max(1, Math.round(img.naturalWidth * escala));
        const altura = Math.max(1, Math.round(img.naturalHeight * escala));
        const canvas = document.createElement("canvas");
        canvas.width = largura; canvas.height = altura;
        const ctx = canvas.getContext("2d", {alpha:false});
        ctx.fillStyle = "#fff"; ctx.fillRect(0,0,largura,altura);
        ctx.drawImage(img,0,0,largura,altura);
        return canvas.toDataURL("image/jpeg", 0.78);
    }

    async function inserirImagemNoEditor(arquivo){
        if(!arquivo) return;
        try{
            const otimizada = await arquivoImagemOtimizado(arquivo);
            const id = `figura-${Date.now()}-${Math.random().toString(16).slice(2)}`;
            inserirHtmlNoCursor(`<figure id="${id}" style="width:min(100%,680px);height:auto"><img src="${otimizada}" alt="Imagem do enunciado"></figure><p><br></p>`);
            const figura = document.getElementById(id);
            if(figura) prepararFigura(figura);
        }catch(erro){
            avisoArk(erro.message || "Não foi possível inserir a imagem.", editor);
        }
    }

    function analisarQuestaoColada(bruto){
        const texto = String(bruto || "").replace(/\r/g, "").trim();
        if(!texto) return null;
        const linhas = texto.split("\n");
        const alternativas = [];
        const enunciadoLinhas = [];
        let atual = null;
        let gabarito = "";
        const regexAlternativa = /^\s*(?:alternativa\s+)?([A-Ha-h])\s*[\)\.\-:]\s*(.*)$/i;
        const regexGabarito = /^\s*(?:gabarito|resposta|alternativa correta|resposta correta)\s*[:\-]?\s*(?:alternativa\s*)?([A-Ha-h])\s*[\)\.]?\s*$/i;
        linhas.forEach(linha => {
            const gab = linha.match(regexGabarito);
            if(gab){ gabarito = gab[1].toUpperCase(); atual = null; return; }
            const alt = linha.match(regexAlternativa);
            if(alt){
                atual = {letra: alt[1].toUpperCase(), texto: alt[2].trim()};
                alternativas.push(atual);
            }else if(atual && linha.trim()){
                atual.texto += (atual.texto ? " " : "") + linha.trim();
            }else if(!atual){
                enunciadoLinhas.push(linha);
            }
        });
        return alternativas.length >= 2 ? {enunciado: enunciadoLinhas.join("\n").trim(), alternativas, gabarito} : null;
    }

    function preencherQuestaoOrganizada(resultado){
        editor.innerHTML = "";
        resultado.enunciado.split(/\n{2,}/).filter(paragrafo => paragrafo.trim()).forEach(paragrafo => {
            const p = document.createElement("p");
            p.textContent = paragrafo.replace(/\n/g, " ").trim();
            editor.appendChild(p);
        });
        lista.innerHTML = "";
        resultado.alternativas.forEach(itemAlt => {
            const item = criarAlternativa(itemAlt.texto);
            if(resultado.gabarito && itemAlt.letra === resultado.gabarito){
                item.querySelector(".input-correta").checked = true;
            }
        });
        atualizarTipo();
    }

    function atualizarTipo(){
        const tipo = document.querySelector('input[name="tipo_questao"]:checked').value;
        blocoAlternativas.classList.toggle("oculto", !["multipla_escolha","multiplas_respostas"].includes(tipo));
        blocoVF.classList.toggle("oculto", tipo !== "verdadeiro_falso");
        blocoResposta.classList.toggle("oculto", !["resposta_curta","numerica","discursiva"].includes(tipo));
        blocoCriterios.classList.toggle("oculto", tipo !== "discursiva");
        blocoLinhasResposta.classList.toggle("oculto", tipo !== "discursiva");
        document.getElementById("respostaEsperada").required = ["resposta_curta","numerica"].includes(tipo);
        atualizarAlternativas();
    }
    document.querySelectorAll(".editor-btn[data-comando]").forEach(botao => {
        botao.addEventListener("click", () => {
            const cmd=botao.dataset.comando;
            if(figuraSelecionada && ["justifyLeft","justifyCenter","justifyRight"].includes(cmd)){
                figuraSelecionada.style.marginLeft = cmd==="justifyRight" ? "auto" : (cmd==="justifyCenter" ? "auto" : "0");
                figuraSelecionada.style.marginRight = cmd==="justifyLeft" ? "auto" : (cmd==="justifyCenter" ? "auto" : "0");
                return;
            }
            editor.focus(); document.execCommand(cmd, false, null);
        });
    });
    document.getElementById("inserirImagemEnunciado").addEventListener("click", () => arquivoImagemEnunciado.click());
    arquivoImagemEnunciado.addEventListener("change", () => { inserirImagemNoEditor(arquivoImagemEnunciado.files[0]); arquivoImagemEnunciado.value = ""; });

    const observadorFiguras = new MutationObserver(() => {
        editor.querySelectorAll("figure").forEach(prepararFigura);
    });
    observadorFiguras.observe(editor, {childList:true, subtree:true});
    editor.querySelectorAll("figure").forEach(prepararFigura);
    editor.addEventListener("paste", evento => {
        const itens = [...(evento.clipboardData?.items || [])];
        const imagens = itens.filter(item => item.kind === "file" && item.type.startsWith("image/"));
        const texto = evento.clipboardData?.getData("text/plain") || "";
        const organizada = analisarQuestaoColada(texto);

        if(organizada){
            evento.preventDefault();
            preencherQuestaoOrganizada(organizada);
            imagens.forEach(item => inserirImagemNoEditor(item.getAsFile()));
            return;
        }

        if(imagens.length){
            evento.preventDefault();
            imagens.forEach(item => inserirImagemNoEditor(item.getAsFile()));
            if(texto.trim()) document.execCommand("insertText", false, texto);
            return;
        }

        if(texto){
            evento.preventDefault();
            document.execCommand("insertText", false, texto);
        }
    });
    editor.addEventListener("click", evento => {
        if(!evento.target.closest("figure")) limparSelecaoFigura();
    });

    editor.addEventListener("keydown", evento => {
        if(!figuraSelecionada) return;

        if(evento.key === "Delete" || evento.key === "Backspace"){
            evento.preventDefault();
            figuraSelecionada.remove();
            figuraSelecionada = null;
        }
    });

    editor.addEventListener("dragover", evento => {
        const arrastando = editor.querySelector("figure.arrastando");
        if(!arrastando) return;

        evento.preventDefault();
        evento.dataTransfer.dropEffect = "move";

        editor.querySelectorAll(".editor-drop-line").forEach(linha => linha.remove());

        const blocos = [...editor.children].filter(elemento =>
            elemento !== arrastando && !elemento.classList.contains("editor-drop-line")
        );

        let referencia = null;
        for(const bloco of blocos){
            const caixa = bloco.getBoundingClientRect();
            if(evento.clientY < caixa.top + caixa.height / 2){
                referencia = bloco;
                break;
            }
        }

        const linha = document.createElement("div");
        linha.className = "editor-drop-line";
        linha.contentEditable = "false";

        if(referencia){
            editor.insertBefore(linha, referencia);
        }else{
            editor.appendChild(linha);
        }
    });

    editor.addEventListener("drop", evento => {
        const arrastando = editor.querySelector("figure.arrastando");
        const linha = editor.querySelector(".editor-drop-line");
        if(!arrastando) return;

        evento.preventDefault();

        if(linha){
            editor.insertBefore(arrastando, linha);
            linha.remove();
        }

        arrastando.classList.remove("arrastando");
        limparSelecaoFigura();
        arrastando.classList.add("selecionada");
        figuraSelecionada = arrastando;
    });

    const abrirArkIA=document.getElementById("abrirArkIA");
    const modalArkIA=document.getElementById("modalArkIA");
    const fecharArkIA=document.getElementById("fecharArkIA");
    const gerarArkIA=document.getElementById("gerarArkIA");
    const iaTema=document.getElementById("iaTema");
    const iaStatus=document.getElementById("iaStatus");
    const iaCriarImagem=document.getElementById("iaCriarImagem");

    function escSvg(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
    function svgImagemPedagogica(spec){
        if(!spec||!spec.tipo)return null; const d=spec.dados||{}; let body=""; const W=760,H=430;
        const title=escSvg(spec.titulo||"");
        if(spec.tipo==="grid"){const cols=(d.colunas||["A","B","C","D"]).slice(0,10), rows=(d.linhas||["1","2","3","4"]).slice(0,10); const x0=120,y0=90,cw=Math.min(100,520/Math.max(cols.length,1)),rh=Math.min(70,270/Math.max(rows.length,1)); cols.forEach((c,i)=>body+=`<text x="${x0+i*cw+cw/2}" y="75" text-anchor="middle" font-size="18" font-weight="700">${escSvg(c)}</text>`); rows.forEach((r,j)=>body+=`<text x="95" y="${y0+j*rh+rh/2+6}" text-anchor="middle" font-size="18" font-weight="700">${escSvg(r)}</text>`); for(let i=0;i<=cols.length;i++)body+=`<line x1="${x0+i*cw}" y1="${y0}" x2="${x0+i*cw}" y2="${y0+rows.length*rh}" stroke="#334155"/>`; for(let j=0;j<=rows.length;j++)body+=`<line x1="${x0}" y1="${y0+j*rh}" x2="${x0+cols.length*cw}" y2="${y0+j*rh}" stroke="#334155"/>`; (d.itens||[]).forEach(it=>{let i=cols.indexOf(String(it.coluna)),j=rows.indexOf(String(it.linha));if(i>=0&&j>=0){let x=x0+i*cw+cw/2,y=y0+j*rh+rh/2;body+=`<circle cx="${x}" cy="${y}" r="18" fill="#dbeafe" stroke="#17468f"/><text x="${x}" y="${y+5}" text-anchor="middle" font-size="13" font-weight="700">${escSvg((it.rotulo||'').slice(0,3))}</text><text x="${x}" y="${y+35}" text-anchor="middle" font-size="12">${escSvg(it.rotulo||'')}</text>`}});}
        else if(spec.tipo==="number_line"){let a=Number(d.inicio??0),b=Number(d.fim??10);if(b<=a)b=a+10;body+=`<line x1="90" y1="230" x2="680" y2="230" stroke="#0f172a" stroke-width="3"/>`;for(let v=a;v<=b&&v-a<=20;v++){let x=90+(v-a)/(b-a)*590;body+=`<line x1="${x}" y1="218" x2="${x}" y2="242" stroke="#0f172a"/><text x="${x}" y="265" text-anchor="middle" font-size="14">${v}</text>`}(d.destaques||[]).forEach(it=>{let x=90+(Number(it.valor)-a)/(b-a)*590;body+=`<circle cx="${x}" cy="230" r="10" fill="#2563eb"/><text x="${x}" y="195" text-anchor="middle" font-size="15" font-weight="700">${escSvg(it.rotulo||it.valor)}</text>`});}
        else if(spec.tipo==="bar_chart"){let cats=(d.categorias||[]).slice(0,8),vals=(d.valores||[]).map(Number).slice(0,8),m=Math.max(1,...vals);body+=`<line x1="90" y1="340" x2="690" y2="340" stroke="#0f172a"/><line x1="90" y1="90" x2="90" y2="340" stroke="#0f172a"/>`;cats.forEach((c,i)=>{let bw=Math.min(55,500/Math.max(cats.length,1)),x=120+i*(520/Math.max(cats.length,1)),h=(vals[i]||0)/m*210;body+=`<rect x="${x}" y="${340-h}" width="${bw}" height="${h}" fill="#bfdbfe" stroke="#17468f"/><text x="${x+bw/2}" y="365" text-anchor="middle" font-size="13">${escSvg(c)}</text><text x="${x+bw/2}" y="${330-h}" text-anchor="middle" font-size="13" font-weight="700">${vals[i]||0}</text>`});}
        else if(spec.tipo==="timeline"){let ev=(d.eventos||[]).slice(0,6);body+=`<line x1="80" y1="220" x2="690" y2="220" stroke="#17468f" stroke-width="4"/>`;ev.forEach((e,i)=>{let x=100+i*(570/Math.max(ev.length-1,1));body+=`<circle cx="${x}" cy="220" r="9" fill="#2563eb"/><text x="${x}" y="185" text-anchor="middle" font-size="14" font-weight="700">${escSvg(e.ano||'')}</text><text x="${x}" y="255" text-anchor="middle" font-size="12">${escSvg((e.rotulo||'').slice(0,24))}</text>`});}
        else {let fs=(d.formas||[]).slice(0,5);fs.forEach((f,i)=>{let x=120+i*125,y=180,t=f.tipo||"quadrado";if(t==="circulo")body+=`<circle cx="${x}" cy="${y}" r="42" fill="#eff6ff" stroke="#17468f" stroke-width="3"/>`;else if(t==="triangulo")body+=`<polygon points="${x},130 ${x-48},225 ${x+48},225" fill="#eff6ff" stroke="#17468f" stroke-width="3"/>`;else body+=`<rect x="${x-45}" y="${y-40}" width="${t==='retangulo'?100:80}" height="80" fill="#eff6ff" stroke="#17468f" stroke-width="3"/>`;body+=`<text x="${x}" y="285" text-anchor="middle" font-size="14" font-weight="700">${escSvg(f.rotulo||'')}</text>`});}
        return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect width="100%" height="100%" fill="white"/><text x="380" y="38" text-anchor="middle" font-family="Arial" font-size="20" font-weight="700" fill="#0f2f66">${title}</text><g font-family="Arial" fill="#0f172a">${body}</g></svg>`;
    }
    async function inserirImagemIA(spec){const svg=svgImagemPedagogica(spec);if(!svg)return;const blob=new Blob([svg],{type:"image/svg+xml"});const url=URL.createObjectURL(blob),img=new Image();await new Promise((ok,no)=>{img.onload=ok;img.onerror=no;img.src=url});const canvas=document.createElement("canvas");canvas.width=760;canvas.height=430;canvas.getContext("2d").drawImage(img,0,0);URL.revokeObjectURL(url);const fig=document.createElement("figure");fig.className="editor-figura";fig.contentEditable="false";const out=document.createElement("img");out.src=canvas.toDataURL("image/png");out.alt=(spec.descricao||spec.titulo||"Imagem de apoio gerada pela ARK IA");fig.appendChild(out);editor.appendChild(fig);}
    function atualizarContextoIA(){
        document.getElementById("iaCtxDisciplina").textContent=disciplina.value||"—";
        document.getElementById("iaCtxAno").textContent=anoSerie.value||"—";
        document.getElementById("iaCtxDificuldade").textContent=document.getElementById("dificuldadeQuestao").value||"Média";
        document.getElementById("iaCtxHabilidade").textContent=document.getElementById("habilidadeBncc").value||"Não informada";
    }
    function fecharModalIA(){modalArkIA.classList.remove("ativo")}
    abrirArkIA?.addEventListener("click",()=>{atualizarContextoIA();iaStatus.textContent="";iaStatus.className="ia-status";modalArkIA.classList.add("ativo");setTimeout(()=>iaTema.focus(),50)});
    fecharArkIA?.addEventListener("click",fecharModalIA);
    modalArkIA?.addEventListener("click",e=>{if(e.target===modalArkIA)fecharModalIA()});
    gerarArkIA?.addEventListener("click",async()=>{
        const payload={
            disciplina:disciplina.value,
            etapa_ensino:etapa.value,
            ano_serie:anoSerie.value,
            dificuldade:document.getElementById("dificuldadeQuestao").value||"Média",
            habilidade_bncc:document.getElementById("habilidadeBncc").value||"",
            tema:iaTema.value.trim(),
            criar_imagem:!!iaCriarImagem?.checked
        };
        iaStatus.className="ia-status"; iaStatus.textContent="Gerando questão..."; gerarArkIA.disabled=true; gerarArkIA.textContent="Gerando...";
        try{
            const resp=await fetch("/api/ia/gerar-questao",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
            const data=await resp.json().catch(()=>({ok:false,erro:"Resposta inválida do servidor."}));
            if(!resp.ok || !data.ok) throw new Error(data.erro||"Não foi possível gerar a questão.");
            const q=data.questao||{};
            document.querySelector('input[name="tipo_questao"][value="multipla_escolha"]').checked=true;
            editor.innerHTML="";
            const p=document.createElement("p"); p.textContent=q.enunciado||""; editor.appendChild(p);
            if(q.imagem_apoio) await inserirImagemIA(q.imagem_apoio);
            lista.innerHTML="";
            (q.alternativas||[]).forEach((texto,idx)=>{const item=criarAlternativa(texto); if(letra(idx)===q.gabarito)item.querySelector(".input-correta").checked=true;});
            const dif=document.getElementById("dificuldadeQuestao"); if(["Fácil","Média","Difícil"].includes(q.dificuldade)) dif.value=q.dificuldade;
            const hab=document.getElementById("habilidadeBncc"); if(q.habilidade_bncc && !hab.value) hab.value=q.habilidade_bncc;
            const uni=document.getElementById("unidadeTematica"); if(q.unidade_tematica && !uni.value) uni.value=q.unidade_tematica;
            const obj=document.getElementById("objetoConhecimento"); if(q.objeto_conhecimento && !obj.value) obj.value=q.objeto_conhecimento;
            const bloom=document.getElementById("taxonomiaBloom"); if(q.taxonomia_bloom && [...bloom.options].some(o=>o.value===q.taxonomia_bloom)) bloom.value=q.taxonomia_bloom;
            const obs=document.getElementById("observacoesQuestao"); if(q.explicacao && !obs.value) obs.value="Sugestão de justificativa da ARK IA: "+q.explicacao;
            atualizarTipo(); fecharModalIA(); editor.scrollIntoView({behavior:"smooth",block:"center"});
        }catch(err){iaStatus.className="ia-status erro";iaStatus.textContent=err.message||"Não foi possível gerar a questão."}
        finally{gerarArkIA.disabled=false;gerarArkIA.textContent="Gerar questão"}
    });

    etapa.addEventListener("change", atualizarSeries);
    document.querySelectorAll('input[name="tipo_questao"]').forEach(input => input.addEventListener("change", atualizarTipo));
    botaoAdicionar.addEventListener("click", criarAlternativa);

    document.getElementById("formQuestao").addEventListener("submit", evento => {
        const clone = editor.cloneNode(true);
        clone.querySelectorAll("figure").forEach(fig => { fig.classList.remove("selecionada", "arrastando"); fig.removeAttribute("draggable"); fig.removeAttribute("contenteditable"); });
        enunciadoHtml.value = clone.innerHTML.trim();
        enunciadoTexto.value = editor.innerText.replace(/\n{3,}/g, "\n\n").trim();
        if(!enunciadoTexto.value && !clone.querySelector("img")){
            evento.preventDefault();
            editor.focus();
            return avisoArk("Digite o enunciado da questão antes de cadastrar.", editor);
        }
        const tipo=document.querySelector('input[name="tipo_questao"]:checked')?.value;
        if(tipo==="multipla_escolha" && !document.querySelector('input[name="corretas[]"]:checked')){evento.preventDefault(); return avisoArk("Selecione a alternativa correta antes de cadastrar a questão. O que você já preencheu foi mantido.", document.querySelector('.marcar-correta input'));}
        if(tipo==="multiplas_respostas" && !document.querySelector('input[name="corretas[]"]:checked')){evento.preventDefault(); return avisoArk("Marque pelo menos uma alternativa correta antes de cadastrar a questão. O que você já preencheu foi mantido.", document.querySelector('.marcar-correta input'));}
        if(tipo==="verdadeiro_falso" && !document.querySelector('input[name="resposta_vf"]:checked')){evento.preventDefault(); return avisoArk("Selecione Verdadeiro ou Falso antes de cadastrar a questão. O que você já preencheu foi mantido.", document.querySelector('input[name="resposta_vf"]'));}
    });

    atualizarSeries();
    for(let i=0;i<4;i++) criarAlternativa();
    atualizarTipo();
    setTimeout(() => editor.focus(), 80);
});
