import EventEmitter from "events";

function* ids() {
  let id = 0;
  while (true) yield id++;
}

const format = function (value) {
  return String(value);
};

class Queue extends EventEmitter {
  #items = [];

  push(item) {
    this.#items.push(item);
    this.emit("push", item);
  }
}

module.exports = { ids, format, Queue };
