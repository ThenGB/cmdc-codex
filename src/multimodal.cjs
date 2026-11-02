// CommandCode serializes image arrays inside tool outputs as text. Keep image data in image messages.
function normalizeToolImages(input) {
  if (!Array.isArray(input)) return input;
  return input.flatMap(item => {
    if (!['custom_tool_call_output','function_call_output'].includes(item.type) || !Array.isArray(item.output)) return [item];
    const images = item.output.filter(part => part.type === 'input_image');
    if (!images.length) return [item];
    const remaining = item.output.filter(part => part.type !== 'input_image');
    const text = remaining.every(part => part.type === 'input_text')
      ? remaining.map(part => part.text).join('\n') : JSON.stringify(remaining);
    return [
      {...item,output:text || 'Tool images attached in the following message.'},
      {type:'message',role:'user',content:[{type:'input_text',text:'Images returned by the preceding tool call (tool output):'},...images]},
    ];
  });
}
module.exports={normalizeToolImages};
