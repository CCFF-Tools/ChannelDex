const assert = require('assert');
const intake = require('../pubtv/static/connected_intake.js');
assert.deepStrictEqual(intake.splitPaths(' /a \n\n/b\n'), ['/a', '/b']);
assert.deepStrictEqual(intake.mergePaths('/a\n/b', ['/b', '/c']), ['/a', '/b', '/c']);
assert.deepStrictEqual(intake.stateFor({step: 'inspect', rows: [1]}), {step: 'inspect', rows: [1]});
assert.deepStrictEqual(intake.visibleEpisodeIds([
  {id: '', showId: ''}, {id: 1, showId: 10}, {id: 2, showId: 20}
], 10), ['', '1']);
