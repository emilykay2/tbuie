#!/usr/bin/python3

import json
import flask
import random
import os
import ankura
import time
from tqdm import tqdm
import sys
import tempfile

app = flask.Flask(__name__, static_url_path='')

dataset_name = sys.argv[1]

train_size = 10000
test_size = 500
number_of_topics = 50
label_weight = 1
smoothing = 0

if sys.argv[1]=='newsgroups':
    attr_name = 'coarse_newsgroup'
elif sys.argv[1]=='yelp':
    attr_name = 'binary_rating'
elif sys.argv[1]=='tripadvisor':
    attr_name = 'label'

@ankura.util.pickle_cache(f'newsgroups_train{train_size}_test{test_size}_k{number_of_topics}_lw{label_weight}_smoothing{smoothing}.pickle')
def load_newsgroups_data():
    print('***Getting the corpus')
    corpus = ankura.corpus.newsgroups()

    split = ankura.pipeline.test_train_split(corpus, num_train=train_size, num_test=test_size, return_ids=True)
    (train_ids, train_corpus), (test_ids, test_corpus) = split

    Q, labels = ankura.anchor.build_labeled_cooccurrence(corpus, attr_name, train_ids,
                                                        label_weight=label_weight, smoothing=smoothing)

    gs_anchor_indices = ankura.anchor.gram_schmidt_anchors(corpus, Q, k=number_of_topics, return_indices=True)

    gs_anchor_vectors = Q[gs_anchor_indices]
    gs_anchor_tokens = [[corpus.vocabulary[index]] for index in gs_anchor_indices]
    return corpus, Q, labels, train_ids, train_corpus, test_ids, test_corpus, gs_anchor_vectors, gs_anchor_indices, gs_anchor_tokens


@ankura.util.pickle_cache(f'yelp_train{train_size}_test{test_size}_k{number_of_topics}_lw{label_weight}_smoothing{smoothing}.pickle')
def load_yelp_data():
    print('***Getting the corpus')
    corpus = ankura.corpus.yelp()

    split = ankura.pipeline.test_train_split(corpus, num_train=train_size, num_test=test_size, return_ids=True)
    (train_ids, train_corpus), (test_ids, test_corpus) = split

    Q, labels = ankura.anchor.build_labeled_cooccurrence(corpus, attr_name, train_ids,
                                                        label_weight=label_weight, smoothing=smoothing)

    gs_anchor_indices = ankura.anchor.gram_schmidt_anchors(corpus, Q, k=number_of_topics, return_indices=True)

    gs_anchor_vectors = Q[gs_anchor_indices]
    gs_anchor_tokens = [[corpus.vocabulary[index]] for index in gs_anchor_indices]
    return corpus, Q, labels, train_ids, train_corpus, test_ids, test_corpus, gs_anchor_vectors, gs_anchor_indices, gs_anchor_tokens


@ankura.util.pickle_cache(f'tripadvisor_train{train_size}_test{test_size}_k{number_of_topics}_lw{label_weight}_smoothing{smoothing}.pickle')
def load_tripadvisor_data():
    print('***Getting the corpus')
    corpus = ankura.corpus.tripadvisor()

    print('***Splitting Corpus')
    split = ankura.pipeline.test_train_split(corpus, num_train=train_size, num_test=test_size, return_ids=True)
    (train_ids, train_corpus), (test_ids, test_corpus) = split

    print('***Building Labeled Cooccurrence')
    Q, labels = ankura.anchor.build_labeled_cooccurrence(corpus, attr_name, train_ids,
                                                        label_weight=label_weight, smoothing=smoothing)

    print('***Performing Gram-Schmidt')
    gs_anchor_indices = ankura.anchor.gram_schmidt_anchors(corpus, Q, k=number_of_topics, return_indices=True)

    gs_anchor_vectors = Q[gs_anchor_indices]
    gs_anchor_tokens = [[corpus.vocabulary[index]] for index in gs_anchor_indices]
    return corpus, Q, labels, train_ids, train_corpus, test_ids, test_corpus, gs_anchor_vectors, gs_anchor_indices, gs_anchor_tokens


if sys.argv[1]=='newsgroups':
    corpus, Q, labels, train_ids, train_corpus, test_ids, test_corpus, gs_anchor_vectors, gs_anchor_indices, gs_anchor_tokens = load_newsgroups_data()
elif sys.argv[1]=='yelp':
    corpus, Q, labels, train_ids, train_corpus, test_ids, test_corpus, gs_anchor_vectors, gs_anchor_indices, gs_anchor_tokens = load_yelp_data()
elif sys.argv[1]=='tripadvisor':
    corpus, Q, labels, train_ids, train_corpus, test_ids, test_corpus, gs_anchor_vectors, gs_anchor_indices, gs_anchor_tokens = load_tripadvisor_data()


@app.route('/')
def serve_itm():
    return app.send_static_file('index.html')

@app.route('/vocab')
def get_vocab():
    return flask.jsonify(vocab=corpus.vocabulary)

@app.route('/finished', methods=['GET', 'POST'])
def finish():
    data = flask.request.get_json()

    directory = os.path.join('FinalAnchors', sys.argv[1])
    try:
        os.makedirs(directory)
    except FileExistsError:
        pass

    pickle.dump(data, tempfile.NamedTemporaryFile(mode='wb', delete=False,
                                                  prefix=sys.argv[1],
                                                  suffix='.pickle',
                                                  dir=directory))
    return 'OK'


@app.route('/topics')
def topic_request():
    raw_anchors = flask.request.args.get('anchors')

    start=time.time()
    if raw_anchors is None:
        anchor_tokens, anchor_vectors = gs_anchor_tokens, gs_anchor_vectors
    else:
        anchor_tokens = json.loads(raw_anchors)
        anchor_vectors = ankura.anchor.tandem_anchors(anchor_tokens, Q, corpus)
    print('***tadem_anchors:', time.time()-start)

    start=time.time()
    # After change to variational assign, this is most time consuming part
    # (depending on train_corpus size)
    C, topics = ankura.anchor.recover_topics(Q, anchor_vectors, epsilon=1e-5, get_c=True)
    print('C SHAPE :', C.shape)

    print('***recover_topics:', time.time()-start)

    start=time.time()
    topic_summary = ankura.topic.topic_summary(topics[:len(corpus.vocabulary)], corpus)
    print('***topic_summary:', time.time()-start)

    #print('anchors',anchor_tokens)
    #print('topics',topic_summary)

    start=time.time()
    #classifier = ankura.topic.free_classifier_revised(topics, Q, labels)

    classifier = ankura.topic.free_classifier_dream(corpus, attr_name, labeled_docs=train_ids, topics=topics, C=C, labels=labels)
    print('***Get Classifier:', time.time()-start)
    start=time.time()

    ankura.topic.variational_assign(test_corpus, topics)

    print('***variational assign:', time.time()-start)

    contingency = ankura.validate.Contingency()

    start=time.time()
    for doc in test_corpus.documents:
        gold = doc.metadata[attr_name]
        pred = classifier(doc)
        contingency[gold, pred] += 1
    print('***Classify:', time.time()-start)
    print('*~~Accuracy:', contingency.accuracy())

    return flask.jsonify(anchors=anchor_tokens,
                         topics=topic_summary,
                         accuracy=contingency.accuracy())

if __name__ == '__main__':
    if len(sys.argv)>2:
        port = int(sys.argv[2])
    else:
        port=5000
    app.run(debug=True, host='0.0.0.0', port=port)



