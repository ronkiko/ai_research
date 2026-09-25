const $ = (id) => document.getElementById(id);

export function createControls(onSubmit) {
  let affordances = new Map();

  function render(items, disabled) {
    affordances = new Map(items.map((item) => [item.intent_id, item]));
    const select = $('intent');
    const previous = select.value;
    select.replaceChildren();
    for (const item of items) {
      const option = document.createElement('option');
      option.value = item.intent_id;
      option.textContent = item.label;
      select.append(option);
    }
    if (affordances.has(previous)) select.value = previous;
    setDisabled(disabled);
  }

  function setDisabled(disabled) {
    $('send').disabled = Boolean(disabled);
    $('intent').disabled = Boolean(disabled);
  }

  $('composer').onsubmit = (event) => {
    event.preventDefault();
    const intent_id = $('intent').value;
    const affordance = affordances.get(intent_id);
    if (!affordance) return;
    let text = $('message').value.trim();
    if (!text) text = affordance.default_text || '';
    if (!text) return;
    onSubmit({text, intent_id});
  };

  $('message').onkeydown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $('composer').requestSubmit();
    }
  };

  return {render, setDisabled};
}
