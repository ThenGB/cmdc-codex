const {test}=require('node:test');
const assert=require('node:assert/strict');
const {normalizeToolImages}=require('./multimodal.cjs');
for(const type of ['custom_tool_call_output','function_call_output'])test(type+' retains text, image bytes and call order without base64 in tool text',()=>{
 const image={type:'input_image',image_url:'data:image/png;base64,FAKE_FIXTURE_ONLY',detail:'original'};
 const tool={type,call_id:'call-1',output:[{type:'input_text',text:'result before image'},image,{type:'input_text',text:'result after image'}]};
 const next={type:'message',role:'user',content:'Continue'};
 const source=[tool,next],result=normalizeToolImages(source);
 assert.equal(result.length,3);
 assert.equal(result[0].call_id,'call-1');
 assert.equal(result[0].output,'result before image\nresult after image');
 assert(!result[0].output.includes('base64'));
 assert.deepEqual(result[1].content[1],image);
 assert.equal(result[2],next);
 assert.equal(source[0],tool);assert.equal(tool.output.length,3);
});
test('ordinary inputs and non-image tool results remain unchanged',()=>{
 assert.equal(normalizeToolImages('text'),'text');
 const tool={type:'function_call_output',output:[{type:'input_text',text:'result'}]};
 assert.equal(normalizeToolImages([tool])[0],tool);
});
