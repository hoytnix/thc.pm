String.prototype.capitalize = function () {
  return this.replace(/(^|\s)\S/g, function (t) {
    return t.toUpperCase();
  });
};

function sizeBytes(obj) {
  return JSON.stringify(obj).length;
}
