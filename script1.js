
document.addEventListener("DOMContentLoaded", () => {
    const disciplina=document.getElementById("disciplinaQuestao"), etapa=document.getElementById("etapaEnsino"), ano=document.getElementById("anoSerie");
    const habilidade=document.getElementById("habilidadeBncc"), lista=document.getElementById("listaHabilidadesBncc"), unidade=document.getElementById("unidadeTematica"), objeto=document.getElementById("objetoConhecimento"), ajuda=document.getElementById("ajudaUnidadeBncc");
    if(!habilidade || !lista) return;
    let resultados=[], controle=null, timer=null;

    function selecionar(h){
        if(!h) return;
        habilidade.value=h.codigo || h.valor || "";
        objeto.value=h.objeto_conhecimento||"";
        unidade.value=h.unidade_tematica||"";
        ajuda.className="ajuda bncc-status sucesso";
        ajuda.textContent=`${h.codigo}: habilidade selecionada. Objeto do conhecimento e unidade temática foram preenchidos automaticamente.`;
        lista.classList.remove("ativo");
    }
    function desenhar(itens){
        lista.innerHTML=""; resultados=itens||[];
        if(!resultados.length){
            lista.innerHTML='<div class="bncc-vazio">Nenhuma habilidade encontrada. Confira o código ou tente uma palavra da descrição.</div>';
            lista.classList.add("ativo"); return;
        }
        resultados.forEach((h,idx)=>{
            const b=document.createElement("button"); b.type="button"; b.className="bncc-sugestao"; b.dataset.indice=String(idx);
            const codigo=document.createElement("strong"); codigo.textContent=h.codigo||"";
            const desc=document.createElement("span"); desc.textContent=h.descricao||"";
            b.append(codigo,desc); b.addEventListener("mousedown",e=>e.preventDefault()); b.addEventListener("click",()=>selecionar(h)); lista.appendChild(b);
        });
        lista.classList.add("ativo");
    }
    async function pesquisar(forcarLista=false){
        const termo=habilidade.value.trim();
        if(termo.length<2 && !forcarLista){ lista.classList.remove("ativo"); ajuda.className="ajuda bncc-status"; ajuda.textContent="Digite pelo menos 2 caracteres do código ou da descrição, ou clique no campo para ver a lista."; return; }
        if(controle) controle.abort(); controle=new AbortController();
        ajuda.className="ajuda bncc-status"; ajuda.textContent="Pesquisando habilidade BNCC...";
        const q=new URLSearchParams({q:termo,componente:disciplina?.value||"",etapa:etapa?.value||"",ano_serie:ano?.value||""});
        try{
            const r=await fetch('/api/bncc/buscar?'+q,{signal:controle.signal,headers:{'Accept':'application/json'}});
            const d=await r.json(); if(!r.ok) throw new Error(d.erro||"Falha na pesquisa");
            desenhar(d.habilidades||[]);
            if((d.habilidades||[]).length) ajuda.textContent=`${d.habilidades.length} habilidade(s) encontrada(s). Clique em uma opção abaixo.`;
            else {ajuda.className="ajuda bncc-status erro"; ajuda.textContent="Nenhuma habilidade encontrada para essa busca.";}
            const exata=(d.habilidades||[]).find(h=>String(h.codigo||"").toUpperCase()===termo.toUpperCase());
            if(exata) selecionar(exata);
        }catch(e){
            if(e.name!=="AbortError"){ lista.classList.remove("ativo"); ajuda.className="ajuda bncc-status erro"; ajuda.textContent="Não foi possível pesquisar as habilidades BNCC. Atualize a página e tente novamente."; }
        }
    }
    habilidade.addEventListener("input",()=>{ objeto.value=""; unidade.value=""; clearTimeout(timer); timer=setTimeout(pesquisar,220); });
    habilidade.addEventListener("focus",()=>{ if(resultados.length) lista.classList.add("ativo"); else pesquisar(true); });
    habilidade.addEventListener("click",()=>{ if(!resultados.length) pesquisar(true); else lista.classList.add("ativo"); });
    document.addEventListener("click",e=>{ if(!e.target.closest('.bncc-busca-wrap')) lista.classList.remove("ativo"); });
    [disciplina,etapa,ano].filter(Boolean).forEach(el=>el.addEventListener("change",()=>{ if(habilidade.value.trim().length>=2) pesquisar(); }));
});
