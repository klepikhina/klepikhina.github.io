---
layout: default
title: climb_log
category: climb_log
---

{% raw %}
{% for post in site.posts %}
  {% if post.categories contains 'climb_log' %}
	<div class="post">
		<h3 class="title"><a href="{{ post.url }}">{{ post.title }}</a></h3>
	</div>
  {% endif %}
{% endfor %}
{% endraw %}
