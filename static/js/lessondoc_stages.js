/** One bounded stage-text contract shared by creation, management and editing. */
export function parseStagesText(text) {
    const stages=[];
    for(const [index,raw] of String(text||'').split('\n').entries()) {
        if(!raw.trim())continue;
        const match=raw.trim().match(/^(.+?)[:：]\s*(.+)$/);
        if(!match)throw new Error('第 '+(index+1)+' 行请使用“阶段名: 1-4, 6”的格式。');
        const lessons=[];
        for(const part of match[2].replace(/\s*([-~—～])\s*/g,'$1').split(/[,，、\s]+/).filter(Boolean)) {
            const range=part.match(/^(\d+)(?:[-~—～](\d+))?$/);
            if(!range)throw new Error('第 '+(index+1)+' 行的课次编号无效：'+part);
            const a=Number(range[1]),b=Number(range[2]||range[1]);
            if(a<1||b<1||a>200||b>200)throw new Error('课次编号须在 1—200 之间。');
            for(let n=Math.min(a,b);n<=Math.max(a,b);n++)lessons.push(n);
        }
        stages.push({label:match[1].trim(),lessons:[...new Set(lessons)]});
        if(stages.length>200)throw new Error('阶段数量不能超过 200。');
    }
    return stages;
}
export function stagesToText(stages) {
    return(stages||[]).map(stage=>{
        const nums=[...new Set(stage.lessons||[])].sort((a,b)=>a-b),parts=[];
        let start=nums[0];
        for(let i=1;i<=nums.length;i++)if(i===nums.length||nums[i]!==nums[i-1]+1){const end=nums[i-1];parts.push(start===end?String(start):start+'-'+end);start=nums[i];}
        return stage.label+': '+parts.join(', ');
    }).join('\n');
}
