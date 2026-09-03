/** A lesson belongs to one stage, matching the manifest normalization contract. */
export function assignStageLesson(stages,index,lesson,selected) {
    for(const [i,stage]of stages.entries()){
        const values=new Set(stage.lessons||[]);
        if(i===index&&selected)values.add(lesson);
        else if(i===index||selected)values.delete(lesson);
        stage.lessons=[...values].sort((a,b)=>a-b);
    }
}
