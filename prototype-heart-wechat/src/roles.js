export const ageBands = [
 ['child','12岁及以下','儿童'],['teen','13–17岁','少年'],['young','18–24岁','青年'],['urban','25–34岁','都市青年'],
 ['mature','35–44岁','成熟'],['middle','45–54岁','中年'],['older','55–64岁','年长'],['senior','65岁及以上','长辈'],
];
const existing={'young-female':'relationship-glass/young-female-glass-v3','urban-female':'sister','middle-male':'uncle','middle-female':'aunt'};
const ageRoles=ageBands.flatMap(([age,range,label])=>['male','female'].map(gender=>({
 id:`${age}-${gender}`,age,range,gender,name:age==='senior'?(gender==='male'?'爷爷':'奶奶'):`${label}${gender==='male'?'男':'女'}`,
 image:`/assets/${existing[`${age}-${gender}`]||`${age}-${gender}`}.png`,
})));
export const roles=[...ageRoles,
 {id:'unknown-female',age:'preset',range:'默认外观 · 年龄未知',gender:'female',name:'黑长直御姐',image:'/assets/unknown-female-v1.png'},
 {id:'group-default',age:'preset',range:'群聊默认形象',gender:'group',name:'群聊伙伴',image:'/assets/group-default-v1.png'},
];
